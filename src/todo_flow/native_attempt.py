"""One host-owned native attempt, run inside the existing process supervisor.

The server inherits this supervised process group. The visible client receives
only an exact remote socket/thread, never the worker prompt or local execution.
"""

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from .app_server_connection import AppServerConnection
from .maintenance import write_json
from .native_proposal import NativeProposalBinding
from .native_recovery import recover
from .store import Store
from .unix_websocket import UnixWebSocket
from .workspace_creation import _write_exclusive


DISABLED = (
    "apps",
    "plugins",
    "multi_agent",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "image_generation",
    "hooks",
    "code_mode",
)


def server_overrides(workspace):
    """Keep worker policy separate from Codex's existing login storage."""
    settings = [
        'approval_policy="never"',
        'sandbox_mode="read-only"',
        "project_doc_max_bytes=0",
        'web_search="disabled"',
        "analytics.enabled=false",
        'developer_instructions=""',
        "notify=[]",
        "projects={" + json.dumps(workspace) + '={trust_level="untrusted"}}',
        *(f"features.{name}=false" for name in DISABLED),
    ]
    return [part for setting in settings for part in ("-c", setting)]


def _orca(spec, args):
    process = subprocess.run(
        [spec["cli"], *args, "--json"],
        cwd=spec["workspace"],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    result = json.loads(process.stdout)
    if result.get("ok") is not True:
        raise RuntimeError("Native viewer request was not confirmed")
    return result


def retire_viewer(spec, record, viewer, folder):
    """Retire the dedicated exec client after its owned server has stopped.

    Codex reconnects when the server exits and can change its own title. Neither
    behavior transfers ownership of this exact client to a reusable shell.
    """
    if type(record.get("server_exit")) is not int:
        raise RuntimeError("Native server termination was not confirmed")
    keys = ("handle", "tabId", "incarnationId", "worktreeId", "executionHostId")

    def inventory():
        response = _orca(spec, ["terminal", "list"])
        rows = response["result"]["terminals"]
        if (
            response.get("_meta", {}).get("runtimeId") != record["runtime_id"]
            or response["result"].get("truncated") is not False
            or response["result"].get("totalCount") != len(rows)
        ):
            raise RuntimeError("Native viewer inventory is not complete")
        return rows

    same_tab = [row for row in inventory() if row.get("tabId") == viewer["tabId"]]
    if len(same_tab) != 1 or any(same_tab[0].get(key) != viewer[key] for key in keys):
        raise RuntimeError("Native viewer tab ownership changed; preserve it")
    shown = _orca(spec, ["terminal", "show", "--terminal", viewer["handle"]])
    current = shown["result"]["terminal"]
    if shown.get("_meta", {}).get("runtimeId") != record["runtime_id"] or any(
        current.get(key) != viewer[key] for key in keys
    ):
        raise RuntimeError("Native viewer identity changed; preserve it")
    last_input = current.get("lastInputAt")
    if current.get("orphaned") is not False or (
        last_input is not None
        and (
            type(last_input) not in (int, float)
            or not math.isfinite(last_input)
            or last_input > record["viewer_started_at"] * 1000
        )
    ):
        raise RuntimeError("Native viewer activity changed; preserve it")
    exited = False
    if current.get("connected") is False and current.get("writable") is False:
        waited = _orca(
            spec,
            [
                "terminal",
                "wait",
                "--terminal",
                viewer["handle"],
                "--for",
                "exit",
                "--timeout-ms",
                "5000",
            ],
        )
        exited = (
            waited.get("_meta", {}).get("runtimeId") == record["runtime_id"]
            and waited["result"]["wait"].get("satisfied") is True
            and waited["result"]["wait"].get("status") == "exited"
        )
        if not exited:
            raise RuntimeError("Native viewer exit was not confirmed")
    elif current.get("connected") is not True or current.get("writable") is not True:
        raise RuntimeError("Native viewer process state is unknown")
    # The user permits input racing this checked close. The exec command cannot
    # return to a user shell; a live client must have a confirmed PTY kill.
    _write_exclusive(
        folder / "native-viewer-close-intent.json",
        {"terminal": viewer, "runtime_id": record["runtime_id"]},
    )
    closed = _orca(spec, ["terminal", "close", "--terminal", viewer["handle"]])
    _write_exclusive(folder / "native-viewer-close.json", closed)
    receipt = closed["result"]["close"]
    if (
        closed.get("_meta", {}).get("runtimeId") != record["runtime_id"]
        or receipt.get("handle") != viewer["handle"]
        or receipt.get("tabId") != viewer["tabId"]
        or (not exited and receipt.get("ptyKilled") is not True)
        or any(row.get("tabId") == viewer["tabId"] for row in inventory())
    ):
        raise RuntimeError("Native viewer removal was not confirmed")
    _write_exclusive(
        folder / "native-viewer-retired.json",
        {
            "handle": viewer["handle"],
            "runtime_id": record["runtime_id"],
            "close_receipt": str(folder / "native-viewer-close.json"),
            "complete_inventory_absent": True,
        },
    )


def _current(spec, binding):
    store = Store(spec["state"])
    with store.transaction() as connection:
        store.assert_claim(connection, spec["task"])
    head = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "HEAD"],
        cwd=spec["workspace"],
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout.strip()
    return replace(binding, head=head)


def run(spec):
    folder = Path(spec["folder"])
    deadline = time.monotonic() + spec["timeout"]
    journal_path = folder / "native-session.json"
    record = {
        "version": 1,
        "task": spec["task"],
        "head": spec["head"],
        "worktree": spec["worktree"],
        "host": "local",
        "status": "prepared",
    }
    _write_exclusive(journal_path, record)
    home = Path(spec["codex_home"])
    socket_directory = Path(tempfile.mkdtemp(prefix="tf-native-", dir="/tmp"))
    socket_path = socket_directory / "server.sock"
    server = None
    client = None
    viewer = None
    result = None
    cleanup_error = None
    env = dict(os.environ)
    env.update(CODEX_HOME=str(home), TERM="xterm-256color")

    def save(status, **fields):
        record.update(status=status, **fields)
        write_json(journal_path, record)
        launch_path = folder / "launch.json"
        launch = json.loads(launch_path.read_text())
        launch.update(execution_mode="orca-native", status=status, worktree=spec["worktree"])
        for key in ("session", "turn", "terminal"):
            if key in record:
                launch[key] = record[key]
        write_json(launch_path, launch)

    def send(connection, message):
        # Never replay a request if this durable record or send becomes uncertain.
        name = str(message.get("id", "initialized"))
        _write_exclusive(folder / ("native-request-" + name + ".json"), message)
        connection.send(message)

    try:
        argv = [
            spec["codex"],
            "app-server",
            "--listen",
            "unix://" + str(socket_path),
            *server_overrides(spec["workspace"]),
        ]
        save("server-intent", socket=str(socket_path), argv=argv)
        with (folder / "native-server.log").open("wb") as log:
            # No new session/process group: the outer supervisor owns all children.
            server = subprocess.Popen(
                argv,
                cwd=spec["workspace"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
            save("server-started", server_pid=server.pid)
            while not socket_path.exists():
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Native server did not expose its owned socket")
                time.sleep(0.05)
            socket_identity = (socket_path.stat().st_dev, socket_path.stat().st_ino)
            client = UnixWebSocket(socket_path, deadline=deadline)
            connection = AppServerConnection(max_buffer_bytes=16_000_000)
            send(client, connection.initialize())
            while connection.state != "initialized-response":
                connection.receive(client.receive())
            send(client, connection.initialized())
            send(client, connection.read_config(spec["workspace"]))
            while connection.state != "ready":
                connection.receive(client.receive())
            request = connection.start_thread(spec["workspace"])
            if spec.get("model"):
                request["params"]["model"] = spec["model"]
            send(client, request)
            while connection.state != "thread-ready":
                message = client.receive()
                if message.get("id") == request["id"]:
                    response = message.get("result", {})
                    if (
                        response.get("approvalPolicy") != "never"
                        or response.get("sandbox") != {"type": "readOnly", "networkAccess": False}
                        or Path(response.get("cwd", "")).resolve()
                        != Path(spec["workspace"]).resolve()
                        or response.get("instructionSources")
                    ):
                        raise ValueError("Native thread did not confirm isolated read-only policy")
                connection.receive(message)
            save("thread-created", session=connection.thread_id)
            send(client, connection.start_turn(spec["workspace"], Path(spec["input"]).read_text()))
            while connection.state != "binding-pending":
                connection.receive(client.receive())
            binding = NativeProposalBinding(
                attempt=spec["task"]["attempt"],
                task=spec["task"]["id"],
                generation=spec["task"]["generation"],
                head=spec["head"],
                kind=spec["task"]["kind"],
                host="local",
                worktree=spec["worktree"],
                dispatch=spec["execution"],
                session=connection.thread_id,
                turn=connection.turn_id,
            )
            connection.bind(
                binding,
                implementation_sessions=frozenset(
                    tuple(x) for x in spec["implementation_sessions"]
                ),
            )
            save("turn-accepted", turn=connection.turn_id, viewer_started_at=time.time())
            command = "exec " + shlex.join(
                [
                    "env",
                    "CODEX_HOME=" + str(home),
                    spec["codex"],
                    "resume",
                    binding.session,
                    "--remote",
                    "unix://" + str(socket_path),
                    "--cd",
                    spec["workspace"],
                    "--no-alt-screen",
                ]
            )
            _write_exclusive(
                folder / "native-viewer-intent.json",
                {"command": command, "session": binding.session},
            )
            created = _orca(
                spec,
                [
                    "terminal",
                    "create",
                    "--worktree",
                    "id:" + spec["worktree"],
                    "--title",
                    spec["title"],
                    "--command",
                    command,
                ],
            )
            _write_exclusive(folder / "native-viewer-response.json", created)
            candidate = created["result"]["terminal"]
            if (
                any(
                    not isinstance(candidate.get(key), str) or not candidate[key]
                    for key in (
                        "handle",
                        "tabId",
                        "incarnationId",
                        "worktreeId",
                        "executionHostId",
                        "title",
                    )
                )
                or candidate["worktreeId"] != spec["worktree"]
                or candidate["executionHostId"] != "local"
                or candidate["title"] != spec["title"]
                or not isinstance(created.get("_meta", {}).get("runtimeId"), str)
                or not created["_meta"]["runtimeId"]
            ):
                raise ValueError("Native viewer ownership was not confirmed")
            viewer = candidate
            save(
                "viewer-accepted",
                terminal=viewer,
                runtime_id=created.get("_meta", {}).get("runtimeId"),
            )
            while True:
                try:
                    result = connection.proposal(current=_current(spec, binding))
                    break
                except ValueError as error:
                    if str(error) != "App Server proposal is not complete":
                        raise
                try:
                    connection.receive(client.receive())
                except (EOFError, OSError):
                    current_socket = socket_path.stat()
                    if (
                        server.poll() is not None
                        or (current_socket.st_dev, current_socket.st_ino) != socket_identity
                    ):
                        raise RuntimeError("Native server identity cannot be recovered")
                    client.close()
                    save("reconciling")
                    result = recover(
                        socket_path,
                        deadline=deadline,
                        home=home,
                        folder=folder,
                        binding=binding,
                        current=lambda: _current(spec, binding),
                        implementation_sessions=frozenset(
                            tuple(x) for x in spec["implementation_sessions"]
                        ),
                    )
                    save("proposal-received", recovery_source="same-server-full-history")
                    break
            save("proposal-received")
    finally:
        if client is not None:
            client.close()
        if server is not None:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            record["server_exit"] = server.returncode
        if viewer is not None:
            try:
                retire_viewer(spec, record, viewer, folder)
            except Exception as error:
                cleanup_error = str(error)
        # The outer live supervisor proves group cleanup, including tool descendants.
        shutil.rmtree(socket_directory)
        save("server-stopped", viewer_cleanup_error=cleanup_error)
    if result is None:
        raise RuntimeError("Native attempt has no complete proposal")
    if _current(spec, binding) != binding:
        raise ValueError("Native candidate HEAD changed during cleanup")
    write_json(folder / "native-proposal.json", result)
    save("complete")


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()))
