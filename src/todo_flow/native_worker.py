"""Select and supervise the supported Orca/Codex native adapter before effects."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .launchers import orca_result
from .maintenance import write_json
from .managed_workspace import receipt_path
from .process_inventory import launch_identity
from .store import Store
from .supervised_process import SupervisedProcess
from .terminal_capacity import reserve_terminal
from .workspace_creation import WorkspaceCreationGate, _write_exclusive


SUPPORTED_CODEX = "codex-cli 0.157.1"


def preflight(config, context, task, state, launcher):
    """Return trusted launch inputs or an explicit pre-launch compatibility reason."""
    if launcher.get("backend") != "orca":
        return None, "not_orca"
    if (
        launcher.get("selection", {})
        .get("orca", {})
        .get("advertised", {})
        .get("custom_terminal_command")
        is not True
    ):
        return None, "native_contract_probe_failed"
    ownership = receipt_path(WorkspaceCreationGate(state, task["track"]))
    if not ownership.is_file() or ownership.is_symlink():
        return None, "native_managed_workspace_required"
    codex = shutil.which("codex")
    if not codex:
        return None, "native_codex_missing"
    try:
        version = subprocess.run(
            [codex, "--version"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        if version != SUPPORTED_CODEX:
            return None, "native_codex_version_unverified"
        owned = json.loads(ownership.read_text())
        if type(owned.get("version")) is not int or owned["version"] != 1:
            raise ValueError("Unsupported managed ownership")
        observation = owned["observation"]
        if (
            owned["track"] != task["track"]
            or Path(observation["path"]).resolve() != Path(context["workspace"]).resolve()
        ):
            raise ValueError("Managed ownership does not match worker")
        shown = orca_result(
            launcher["cli"],
            ["worktree", "show", "--worktree", "id:" + observation["id"]],
            context["workspace"],
        )["worktree"]
        if (
            shown.get("id") != observation["id"]
            or shown.get("hostId") != "local"
            or shown.get("instanceId") != observation["instanceId"]
            or shown.get("head") != context["head"]
        ):
            raise ValueError("Native workspace identity changed")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None, "native_contract_probe_failed"
    source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
    if source.is_symlink() or (not source.is_file() and not os.environ.get("OPENAI_API_KEY")):
        return None, "native_auth_storage_unsupported"
    implementation_sessions = []
    if task["kind"] == "review":
        # Only host-produced native records supply session provenance. Older exec
        # implementation sessions retain the existing fresh review adapter.
        for record_path in (Path(state) / "attempts").glob("*/native-session.json"):
            record = json.loads(record_path.read_text())
            previous = record.get("task", {})
            if (
                record.get("version") == 1
                and previous.get("track") == task["track"]
                and previous.get("kind") == "work"
                and record.get("session")
            ):
                implementation_sessions.append([record["host"], record["session"]])
        if not implementation_sessions:
            return None, "native_review_provenance_unavailable"
    return {
        "codex": codex,
        "cli": launcher["cli"],
        "worktree": shown["id"],
        "auth_source": str(source) if source.is_file() else None,
        "implementation_sessions": implementation_sessions,
    }, "native_supported"


def run_native(config, context, task, state, folder, launcher, on_pid):
    supported, reason = preflight(config, context, task, state, launcher)
    selection = {**launcher.get("selection", {}), "reason": reason, "native_ready": bool(supported)}
    launcher["selection"] = selection
    write_json(folder / "launch.json", {**launcher, "status": "selected"})
    if supported is None:
        return None
    identity = launch_identity(state, task)
    # Charge the same capacity ledger used by command terminals before the
    # native adapter can create a visible client. Uncertainty retains this lease.
    reservation = reserve_terminal(launcher, identity, folder)
    slots, lease = reservation
    write_json(
        folder / "launch.json",
        {
            **launcher,
            "status": "selected",
            "terminal_slot": lease,
            "terminal_ledger": str(slots.path),
        },
    )
    # Per-task intent spans replacement attempts: uncertain delivery is never
    # hidden by creating a second server/thread or switching to codex exec.
    intent = Path(state) / ("native-task-" + task["id"] + ".json")
    _write_exclusive(
        intent,
        {
            "version": 1,
            "task": task,
            "head": context["head"],
            "workspace": context["workspace"],
            "worktree": supported["worktree"],
        },
    )
    spec = {
        **supported,
        "terminal_limits": slots.limits,
        "folder": str(folder),
        "state": str(state),
        "task": task,
        "head": context["head"],
        "workspace": context["workspace"],
        "input": str(folder / "input.json"),
        "execution": identity["execution"],
        "model": config["worker"].get("model"),
        "timeout": config.get("worker_timeout", 600),
        "title": f"TODO {task['track']} · {task['kind']} · {task['attempt'][-8:]}",
    }
    spec_path = folder / "native-spec.json"
    _write_exclusive(spec_path, spec)
    with (folder / "output.json").open("w") as out, (folder / "stderr.log").open("w") as err:
        process = SupervisedProcess(
            [sys.executable, "-m", "todo_flow.native_attempt", str(spec_path)],
            identity=identity,
            cwd=context["workspace"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            timeout=spec["timeout"] + 10,
            env=dict(os.environ),
        )
        if on_pid:
            on_pid(process.pid)
        try:
            deadline = time.monotonic() + spec["timeout"] + 10
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native worker timed out; do not resend")
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.stop()
            proof = folder / "native-viewer-retired.json"
            if proof.is_file():
                launch = json.loads((folder / "launch.json").read_text())
                current = launch["terminal_slot"]
                if current["owner"] != lease["owner"] or current["state"] != "closing":
                    raise RuntimeError("Native capacity ownership changed")
                closed = slots.transition(
                    current,
                    "closed",
                    evidence={
                        "launch_record": str(folder / "launch.json"),
                        "physical_retirement": str(proof),
                        "process_confirmation": str(process.gate.barrier.path),
                    },
                )
                write_json(folder / "launch.json", {**launch, "terminal_slot": closed})
        if process.returncode:
            raise RuntimeError(
                "Native worker failed; inspect its durable session and stderr evidence"
            )
    record = json.loads((folder / "native-session.json").read_text())
    if record.get("status") != "complete" or record.get("task") != task:
        raise RuntimeError("Native proposal has no matching completion receipt")
    store = Store(state)
    with store.transaction() as connection:
        store.assert_claim(connection, task)
    current_head = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "HEAD"],
        cwd=context["workspace"],
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout.strip()
    if current_head != context["head"]:
        raise ValueError("Native proposal candidate HEAD changed")
    write_json(
        folder / "launch.json",
        {
            **json.loads((folder / "launch.json").read_text()),
            "status": "completed",
            "execution_mode": "orca-native",
            "session": record["session"],
            "turn": record["turn"],
            "terminal": record["terminal"],
            "worktree": supported["worktree"],
            "selection": selection,
        },
    )
    from .worker import validate

    return validate(json.loads((folder / "native-proposal.json").read_text()), task["kind"])
