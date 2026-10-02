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
from .store import Store
from .supervised_process import SupervisedProcess
from .workspace_creation import WorkspaceCreationGate, _write_exclusive


def status_hook(cli, workspace):
    status = orca_result(cli, ["agent", "hooks", "status"], workspace)
    hook = Path.home() / ".orca" / "agent-hooks" / "codex-hook.sh"
    if (
        status.get("enabled") is not True
        or not hook.is_file()
        or not any(
            row.get("agent") == "codex" and row.get("managedHooksPresent") is True
            for row in status.get("statuses", [])
        )
    ):
        raise RuntimeError(
            "Orca Codex status hooks are unavailable; sidebar registration needs attention"
        )
    return str(hook)


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
        "codex_home": str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).absolute()),
        "implementation_sessions": implementation_sessions,
        "hook": status_hook(launcher["cli"], context["workspace"]),
    }, "native_supported"


def run_native(config, context, task, state, folder, launcher, on_pid, observation):
    supported, reason = preflight(config, context, task, state, launcher)
    selection = {**launcher.get("selection", {}), "reason": reason, "native_ready": bool(supported)}
    launcher["selection"] = selection
    write_json(folder / "launch.json", {**launcher, "status": "selected"})
    if supported is None:
        return None
    identity = observation.launch(state)
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
        "folder": str(folder),
        "state": str(state),
        "task": task,
        "head": context["head"],
        "workspace": context["workspace"],
        "input": str(folder / "input.json"),
        "execution": identity["execution"],
        "process_identity": identity,
        "model": config["worker"].get("model"),
        "effort": config["worker"].get("effort"),
        "timeout": config.get("worker_timeout"),
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
            timeout=None if spec["timeout"] is None else spec["timeout"] + 10,
            env=dict(os.environ),
        )
        try:
            if on_pid:
                on_pid(process.pid)
            deadline = None if spec["timeout"] is None else time.monotonic() + spec["timeout"] + 10
            next_heartbeat = time.monotonic()
            while process.poll() is None:
                if on_pid and time.monotonic() >= next_heartbeat:
                    on_pid(process.pid)
                    next_heartbeat = time.monotonic() + 1
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Native worker timed out; do not resend")
                time.sleep(0.05)
        finally:
            process.stop()
            # The helper can be killed during cancellation/driver cleanup before
            # its own finally runs. The supervisor's confirmed group exit permits
            # retiring the exact viewer, without inventing an App Server exit code.
            record_path = folder / "native-session.json"
            if record_path.exists() and not (folder / "native-viewer-retired.json").exists():
                record = json.loads(record_path.read_text())
                if record.get("task") == task and record.get("terminal"):
                    from .native_attempt import retire_viewer, wait_for_sidebar

                    record["server_group_exit_confirmed"] = True
                    write_json(record_path, record)
                    if record.get("status") != "complete":
                        wait_for_sidebar(folder, "stopped")
                    try:
                        retire_viewer(
                            spec, record, record["terminal"], folder, group_exit_confirmed=True
                        )
                    except Exception as error:
                        record["viewer_cleanup_error"] = str(error)
                    write_json(record_path, record)
        if process.returncode:
            try:
                failure = json.loads((folder / "native-session.json").read_text()).get("failure")
            except (OSError, ValueError):
                failure = None
            detail = (
                f"{failure['type']}: {failure['message']}"
                if isinstance(failure, dict) and failure.get("type") and failure.get("message")
                else f"process exited {process.returncode}"
            )
            raise RuntimeError(
                f"Native worker failed ({detail}); inspect {folder / 'native-session.json'} "
                f"and {folder / 'stderr.log'}"
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
    selection_path = folder / "worker-selection.json"
    write_json(
        selection_path,
        {
            **json.loads(selection_path.read_text()),
            "provider_confirmed": record.get("provider_confirmed"),
        },
    )
    observation.phase = "decoding"
    return observation.validate(
        json.loads((folder / "native-proposal.json").read_text()), task["kind"]
    )
