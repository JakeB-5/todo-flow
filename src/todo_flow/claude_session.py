"""Prepare and adopt a Claude session in the worker's own Orca terminal."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .launchers import orca_result
from .claude_transcript import ClaudeTranscript, MAX_TRANSCRIPT_BYTES
from .maintenance import write_json
from .managed_workspace import receipt_path
from .store import Store
from .workspace_creation import WorkspaceCreationGate, _write_exclusive


def status_hook():
    hook = Path.home() / ".orca" / "agent-hooks" / "claude-hook.sh"
    return str(hook) if hook.is_file() else None


def prepare(config, context, task, state, folder, launcher, instructions, schema):
    """Only pre-launch unavailability permits the caller's compatibility fallback."""
    ownership = receipt_path(WorkspaceCreationGate(state, task["track"]))
    if not ownership.is_file() or ownership.is_symlink():
        return None, "native_managed_workspace_required"
    owned = json.loads(ownership.read_text())
    observation = owned["observation"]
    if (
        owned.get("version") != 1
        or owned.get("track") != task["track"]
        or Path(observation["path"]).resolve() != Path(context["workspace"]).resolve()
    ):
        raise ValueError("Claude session workspace ownership does not match the request")
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
        or Path(shown.get("path", "")).resolve() != Path(context["workspace"]).resolve()
    ):
        raise ValueError("Claude session workspace identity changed")
    executable = shutil.which("claude")
    if executable is None:
        return None, "claude_executable_missing"
    help_result = subprocess.run(
        [executable, "--help"], capture_output=True, text=True, check=True, timeout=10
    )
    required = ("--safe-mode", "--restricted", "--session-id", "--tools", "--permission-mode")
    if not all(flag in help_result.stdout for flag in required):
        return None, "claude_interactive_contract_unavailable"
    session = str(uuid.uuid4())
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    if list(projects.glob("*/" + session + ".jsonl")):
        raise FileExistsError("Refusing to reuse an existing Claude session")
    title = f"TODO {task['track']} · {task['kind']} · {task['attempt'][-8:]}"
    prompt = (
        f"Read the TODO Flow task and evidence paths in {folder / 'input.json'}. "
        f"Read the response schema in {folder / 'schema.json'}. "
        "Complete this one bounded request and return only the JSON proposal."
    )
    write_json(folder / "schema.json", schema)
    adapter = config["worker"]
    argv = [
        executable,
        "--safe-mode",
        "--restricted",
        "--tools",
        "Read,Glob,Grep",
        "--allowedTools",
        "Read,Glob,Grep",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--add-dir",
        str(Path(state).resolve()),
        "--session-id",
        session,
        "--name",
        title,
        "--system-prompt",
        instructions,
    ]
    if adapter.get("model"):
        argv += ["--model", adapter["model"]]
    if adapter.get("effort"):
        argv += ["--effort", adapter["effort"]]
    argv += ["--", prompt]
    spec = {
        "version": 1,
        "provider": "claude",
        "session": session,
        "projects": str(projects.absolute()),
        "workspace": context["workspace"],
        "worktree": shown["id"],
        "head": context["head"],
        "task": task,
        "prompt": prompt,
        "title": title,
        "cli": launcher["cli"],
        "hook": status_hook(),
    }
    # Persist before terminal creation; retries cannot change provider/attempt and replay.
    _write_exclusive(Path(state) / ("interactive-task-" + task["id"] + ".json"), spec)
    launcher.update(
        worktree="id:" + shown["id"], execution_mode="orca-interactive", session=session
    )
    launcher["selection"] = {
        **launcher.get("selection", {}),
        "reason": "claude_interactive_supported",
        "execution_mode": "orca-interactive",
    }
    return {"argv": argv, "session": spec}, "claude_interactive_supported"


def adopt(folder, context, task, state):
    """Recheck the claim and candidate after physical process-group cleanup."""
    spec = json.loads((folder / "terminal-spec.json").read_text())["interactive"]
    record = json.loads((folder / "claude-session.json").read_text())
    if (
        record.get("version") != 1
        or record.get("status") != "complete"
        or any(record.get(key) != spec[key] for key in ("session", "worktree", "head", "task"))
        or spec["task"] != task
        or spec["head"] != context["head"]
        or not all(
            isinstance(record.get(key), str) and record[key] for key in ("turn", "completion")
        )
    ):
        raise ValueError("Claude proposal has no matching completed session")
    evidence = folder / "claude-transcript.jsonl"
    if evidence.is_symlink() or evidence.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Claude proposal evidence is not a bounded regular transcript")
    contents = evidence.read_bytes()
    recorded_evidence = record.get("evidence", {})
    if (
        recorded_evidence.get("path") != str(evidence)
        or recorded_evidence.get("sha256") != hashlib.sha256(contents).hexdigest()
    ):
        raise ValueError("Claude proposal evidence changed")
    reader = ClaudeTranscript(spec)
    for line in contents.splitlines():
        reader.receive(json.loads(line))
    if reader.finished is None or any(
        record.get(key) != reader.finished[key]
        for key in ("turn", "message", "completion", "model")
    ):
        raise ValueError("Claude proposal evidence belongs to another turn")
    output = json.loads((folder / "output.json").read_text())
    if (
        output.get("session_id") != record["session"]
        or output.get("structured_output") != reader.finished["proposal"]
    ):
        raise ValueError("Claude output does not match its completed session")
    store = Store(state)
    with store.transaction() as connection:
        store.assert_claim(connection, task)
    head = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "HEAD"],
        cwd=context["workspace"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout.strip()
    if head != context["head"]:
        raise ValueError("Claude proposal candidate HEAD changed")
    launch = json.loads((folder / "launch.json").read_text())
    write_json(
        folder / "launch.json",
        {
            **launch,
            "status": "completed",
            "session": record["session"],
            "turn": record["turn"],
            "sidebar": record.get("sidebar"),
        },
    )
    selection = json.loads((folder / "worker-selection.json").read_text())
    write_json(
        folder / "worker-selection.json",
        {**selection, "provider_confirmed": {"model": record.get("model"), "effort": None}},
    )
