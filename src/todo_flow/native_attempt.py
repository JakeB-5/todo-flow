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
import stat
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


def prepare_home(folder, workspace, auth_source):
    home = folder / "native-home"
    home.mkdir(mode=0o700)
    config = (
        'approval_policy = "never"\nsandbox_mode = "read-only"\n'
        'cli_auth_credentials_store = "file"\nproject_doc_max_bytes = 0\n'
        'web_search = "disabled"\n[analytics]\nenabled = false\n[features]\n'
        + "\n".join(name + " = false" for name in DISABLED)
        + "\n[projects."
        + json.dumps(workspace)
        + ']\ntrust_level = "untrusted"\n'
    )
    (home / "config.toml").write_text(config)
    if auth_source:
        fd = os.open(auth_source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                raise ValueError("Unsupported native authentication file")
            out = os.open(home / "auth.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(out, "wb") as destination:
                shutil.copyfileobj(source, destination)
    return home


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
    home = prepare_home(folder, spec["workspace"], spec.get("auth_source"))
    socket_directory = Path(tempfile.mkdtemp(prefix="tf-native-", dir="/tmp"))
    socket_path = socket_directory / "server.sock"
    server = None
    client = None
    viewer = None
    result = None
    cleanup_error = None
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "LANG",
            "TMPDIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "OPENAI_API_KEY",
        )
        if key in os.environ
    }
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
        argv = [spec["codex"], "app-server", "--listen", "unix://" + str(socket_path)]
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
                )["result"]["wait"]
                if waited.get("satisfied") is not True or waited.get("status") != "exited":
                    raise RuntimeError("Native viewer has not exited; preserve it")
                shown = _orca(spec, ["terminal", "show", "--terminal", viewer["handle"]])
                current = shown["result"]["terminal"]
                if shown.get("_meta", {}).get("runtimeId") != record.get("runtime_id") or any(
                    current.get(key) != viewer.get(key)
                    for key in ("tabId", "incarnationId", "worktreeId", "title")
                ):
                    raise RuntimeError("Native viewer identity changed; preserve it")
                last_input = current.get("lastInputAt")
                if (
                    current.get("connected") is not False
                    or current.get("writable") is not False
                    or current.get("orphaned") is not False
                    or (
                        last_input is not None
                        and (
                            type(last_input) not in (int, float)
                            or not math.isfinite(last_input)
                            or last_input > record["viewer_started_at"] * 1000
                        )
                    )
                ):
                    raise RuntimeError("Native viewer activity changed; preserve it")
                closed = _orca(spec, ["terminal", "close", "--terminal", viewer["handle"]])
                _write_exclusive(folder / "native-viewer-close.json", closed)
                inventory = _orca(
                    spec, ["terminal", "list", "--worktree", "id:" + spec["worktree"]]
                )
                rows = inventory["result"]["terminals"]
                if (
                    inventory.get("_meta", {}).get("runtimeId") != record["runtime_id"]
                    or inventory["result"].get("truncated") is not False
                    or inventory["result"].get("totalCount") != len(rows)
                    or any(row.get("tabId") == viewer["tabId"] for row in rows)
                ):
                    raise RuntimeError("Native viewer removal was not confirmed")
            except BaseException as error:
                cleanup_error = error
        # The outer live supervisor proves group cleanup, including tool descendants.
        (home / "auth.json").unlink(missing_ok=True)
        shutil.rmtree(socket_directory)
        save("cleanup-failed" if cleanup_error else "server-stopped")
        if cleanup_error:
            raise RuntimeError("Native viewer cleanup requires reconciliation") from cleanup_error
    if result is None:
        raise RuntimeError("Native attempt has no complete proposal")
    if _current(spec, binding) != binding:
        raise ValueError("Native candidate HEAD changed during cleanup")
    write_json(folder / "native-proposal.json", result)
    save("complete")


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()))
