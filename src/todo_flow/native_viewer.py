"""Project host-observed worker lifecycle into its own Orca terminal sidebar.

Runs as the terminal's exec command, outside the read-only model. It never sends
a prompt or derives completion from terminal text. The managed hook receives
only the exact host-bound session and lifecycle from the attempt journal.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

if __package__:
    from .process_launch import LaunchGate
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from todo_flow.process_launch import LaunchGate


def projection(spec, record):
    """A display projection, never authorization to adopt a worker proposal."""
    if (
        record.get("version") != 1
        or record.get("task") != spec["task"]
        or any(record.get(key) != spec[key] for key in ("session", "turn", "head", "worktree"))
    ):
        raise ValueError("Sidebar source does not match the owned worker session")
    status = record.get("status")
    if status in {"proposal-received", "complete"}:
        return "proposal-received"
    if status == "failed" or record.get("server_group_exit_confirmed"):
        return "stopped"
    if status in {"turn-accepted", "viewer-accepted", "reconciling"}:
        return "working"
    return None


def write_receipt(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def observe_sidebar(spec, pane, expected_state):
    """Read the public sidebar projection, retaining only this owned pane."""
    response = subprocess.run(
        [spec["cli"], "worktree", "ps", "--limit", "1000", "--json"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    body = json.loads(response.stdout)
    if body.get("ok") is not True:
        raise ValueError("Orca sidebar query failed")
    for workspace in body["result"]["worktrees"]:
        if workspace.get("worktreeId") != spec["worktree"]:
            continue
        for agent in workspace.get("agents", []):
            if (
                agent.get("paneKey") == pane
                and agent.get("agentType") == spec.get("provider", "codex")
                and agent.get("prompt") == spec["title"]
                and agent.get("state") == expected_state
            ):
                return {
                    "runtime_id": body.get("_meta", {}).get("runtimeId"),
                    "worktree": spec["worktree"],
                    "agent": agent,
                }
    return None


def publish(spec, state, env):
    if (
        env.get("ORCA_WORKTREE_ID") != spec["worktree"]
        or not env.get("ORCA_PANE_KEY")
        or not env.get("ORCA_TAB_ID")
    ):
        raise ValueError("Viewer was not launched in the bound Orca terminal")
    payload = {"session_id": spec["session"], "cwd": spec["workspace"], "prompt": spec["title"]}
    if spec.get("model"):
        payload["model"] = spec["model"]
    if spec.get("transcript"):
        payload["transcript_path"] = spec["transcript"]
    events = ("SessionStart", "UserPromptSubmit") if state == "working" else ("Stop",)
    for event in events:
        subprocess.run(
            ["/bin/sh", spec["hook"]],
            input=json.dumps({**payload, "hook_event_name": event}),
            text=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
    return observe_sidebar(spec, env["ORCA_PANE_KEY"], "working" if state == "working" else "done")


def follow(spec, stop):
    folder = Path(spec["folder"])
    receipt = folder / "native-sidebar.json"
    last = None
    while not stop.is_set():
        try:
            record = json.loads((folder / "native-session.json").read_text())
            # After driver loss the surviving supervisor can finish while the
            # helper's last journal still says working. Reuse its exact durable
            # group proof; never probe or signal a saved PID to guess an exit.
            if spec.get("process_identity"):
                event = LaunchGate(**spec["process_identity"])._event()
                if (
                    event["state"] == "confirmed"
                    and event["evidence"].get("outcome") == "group-exited"
                ):
                    record = {**record, "server_group_exit_confirmed": True}
            state = projection(spec, record)
            if state is not None and state != last:
                observation = publish(spec, state, dict(os.environ))
                write_receipt(
                    receipt,
                    {
                        "version": 1,
                        "session": spec["session"],
                        "turn": spec["turn"],
                        "task": spec["task"],
                        "head": spec["head"],
                        "state": state,
                        "status": "confirmed" if observation else "unconfirmed",
                        "observation": observation,
                        "viewer_pid": os.getpid(),
                    },
                )
                last = state
        except Exception as error:
            write_receipt(
                receipt,
                {
                    "version": 1,
                    "session": spec["session"],
                    "status": "unconfirmed",
                    "error": str(error),
                },
            )
        stop.wait(0.2)


def run(spec):
    stop = threading.Event()
    thread = threading.Thread(target=follow, args=(spec, stop), daemon=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = spec["codex_home"]
    # The client inherits this terminal's stdin. It observes the same server;
    # no worker prompt, permissions override or second execution is supplied.
    process = subprocess.Popen(spec["argv"], env=env, cwd=spec["workspace"])
    thread.start()
    try:
        return process.wait()
    finally:
        stop.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        thread.join(timeout=1)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
