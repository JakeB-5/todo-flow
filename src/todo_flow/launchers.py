"""Choose a visible terminal when available; never duplicate an uncertain launch."""

import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .orca_capabilities import probe_native_contract
from .process_launch import LaunchGate
from .process_inventory import note_prepared
from .verification import VerificationCleanupError


def orca_command():
    if os.environ.get("ORCA_CLI_COMMAND"):
        return os.environ["ORCA_CLI_COMMAND"]
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    if sys.platform.startswith("linux") and not os.environ.get("ORCA_WORKTREE_ID"):
        return "orca-ide"
    return "orca"


def orca_result(cli, args, cwd):
    completed = subprocess.run(
        [cli, *args, "--json"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Orca could not resolve this project terminal context")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Orca returned an invalid result object")
    return result


class LauncherUnavailable(RuntimeError):
    """Preserve discovery evidence when an explicitly requested route fails."""

    def __init__(self, message, selection):
        super().__init__(message)
        self.selection = dict(selection)


def select_launcher(config, workspace):
    mode = config.get("worker_launcher", "auto")
    if mode not in ("auto", "headless", "orca", "tmux", "terminal"):
        raise ValueError("Unknown worker_launcher: " + str(mode))
    selection = {
        "requested": mode,
        "backend": None,
        "reason": "explicit_launcher",
        "native_ready": False,
        "orca": {"status": "not_probed", "native_ready": False},
    }

    def selected(backend, **values):
        return {
            "backend": backend,
            **values,
            "selection": {**selection, "backend": backend},
        }

    if mode == "headless":
        selection["reason"] = "explicit_headless"
        return selected("headless")
    repo = str(Path(config.get("repo", workspace)).resolve())
    if mode in ("auto", "orca"):
        cli = orca_command()
        selection["orca"] = {"cli": cli, "status": "cli_missing", "native_ready": False}
        selection["reason"] = "cli_missing"
        if shutil.which(cli):
            selection["orca"] = probe_native_contract(cli, repo)
            selection["reason"] = "orca_unavailable"
            try:
                status = orca_result(cli, ["status"], repo)
                if status["app"]["running"] is True and status["runtime"]["reachable"] is True:
                    result = orca_result(
                        cli, ["worktree", "show", "--worktree", "path:" + repo], repo
                    )
                    worktree = result["worktree"]
                    if not isinstance(worktree, dict):
                        raise ValueError("Orca returned an invalid worktree object")
                    host = worktree.get("hostId")
                    selection["host"] = host
                    # Missing host evidence is not proof that the PTY is local.
                    if host != "local":
                        selection["reason"] = (
                            "remote_host_mismatch" if host else "host_unverified"
                        )
                        raise RuntimeError("Orca terminals require a confirmed host-local driver")
                    worktree_id = worktree.get("id")
                    if not isinstance(worktree_id, str) or "::" not in worktree_id:
                        raise ValueError("Orca returned an invalid full worktree ID")
                    selection["reason"] = selection["orca"]["status"]
                    # This remains the existing terminal bridge. Schema discovery
                    # alone must never silently promote it to a native session.
                    return selected(
                        "orca", cli=cli, worktree="id:" + worktree_id, repo=repo
                    )
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
            ) as error:
                selection["error_type"] = type(error).__name__
                if mode == "orca":
                    raise LauncherUnavailable(
                        f"Orca terminal unavailable: {error}", selection
                    ) from error
        if mode == "orca":
            raise LauncherUnavailable(
                "Orca terminal unavailable; start Orca or select headless explicitly",
                selection,
            )
    if mode in ("auto", "terminal") and config.get("terminal_command"):
        argv = config["terminal_command"]
        if (
            not isinstance(argv, list)
            or not all(isinstance(x, str) for x in argv)
            or not any("{command}" in x for x in argv)
        ):
            raise ValueError("terminal_command must be an argv array containing {command}")
        return selected("terminal", argv=argv)
    if mode in ("auto", "tmux") and os.environ.get("TMUX") and shutil.which("tmux"):
        return selected("tmux", socket=os.environ["TMUX"].rsplit(",", 2)[0])
    if mode != "auto":
        raise LauncherUnavailable(
            f"{mode} terminal unavailable; configure a launcher or select headless", selection
        )
    return selected("headless")


class TerminalProcess:
    def __init__(self, folder):
        self.folder = folder
        self.started = time.monotonic()
        self.returncode = None
        self.lease_fd = None

    def close_lease(self):
        if self.lease_fd is not None:
            os.close(self.lease_fd)
            self.lease_fd = None

    def __del__(self):
        if getattr(self, "lease_fd", None) is not None:
            try:
                self.close_lease()
            except OSError:
                pass

    def receipt(self):
        try:
            return json.loads((self.folder / "terminal-process.json").read_text())
        except FileNotFoundError:
            return {}

    @property
    def pid(self):
        return self.receipt().get("pid", 0)

    def poll(self):
        receipt = self.receipt()
        if receipt.get("status") == "cleanup_failed":
            raise VerificationCleanupError(
                "Terminal cleanup is unconfirmed: "
                + str(receipt.get("cleanup_error", "unknown cleanup failure"))
                + f"; inspect {self.folder / 'terminal-process.json'} before retrying"
            )
        if receipt.get("status") == "exited":
            if (
                receipt.get("cleanup_confirmed") is not True
                or type(receipt.get("returncode")) is not int
            ):
                raise VerificationCleanupError(
                    "Terminal exit receipt does not confirm group cleanup; "
                    f"inspect {self.folder / 'terminal-process.json'} before retrying"
                )
            spec = self.folder / "terminal-spec.json"
            identity = (
                json.loads(spec.read_text()).get("launch_identity") if spec.exists() else None
            )
            if identity is not None and LaunchGate(**identity)._event()["state"] != "confirmed":
                raise VerificationCleanupError(
                    "Terminal receipt lacks matching launch confirmation"
                )
            self.close_lease()
            self.returncode = receipt["returncode"]
            return self.returncode
        if not receipt and time.monotonic() - self.started > 30:
            raise VerificationCleanupError(
                "Terminal worker start is unconfirmed; inspect launch.json before retrying"
            )
        return None

    def wait(self, timeout=5):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("terminal worker", timeout)
            time.sleep(0.1)
        return self.returncode

    def stop(self, timeout=15):
        self.close_lease()
        # Only the bridge owns a current Popen object. A persisted PID is not
        # authority to signal a process, even when that PID currently exists.
        (self.folder / "terminal-cancelled").touch()
        deadline = time.monotonic() + timeout
        while True:
            # Missing receipts may be delayed beyond the normal start deadline.
            # Still attempt durable cancellation under the bridge lock.
            if self.receipt() and self.poll() is not None:
                return
            with (self.folder / "terminal-run.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    # Re-read under the bridge's launch lock. With no receipt,
                    # cancellation prevents any late delivery from spawning.
                    # A running receipt without its lock means the owner died;
                    # descendants may still exist and must not be guessed at.
                    if not self.receipt():
                        spec_path = self.folder / "terminal-spec.json"
                        if spec_path.exists():
                            identity = json.loads(spec_path.read_text()).get("launch_identity")
                            if identity is not None:
                                LaunchGate(**identity).cancel_pending()
                        return
                    if self.poll() is not None:
                        return
                    raise VerificationCleanupError(
                        "Terminal bridge stopped without confirmed group cleanup; "
                        f"inspect {self.folder / 'terminal-process.json'} before retrying"
                    )
            if time.monotonic() >= deadline:
                raise VerificationCleanupError(
                    "Terminal cancellation did not confirm group cleanup; "
                    f"inspect {self.folder / 'terminal-process.json'} before retrying"
                )
            time.sleep(0.1)


def spawn_terminal(launcher, argv, workspace, folder, title, *, launch_identity=None):
    argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    spec = {"argv": argv, "cwd": workspace, "title": title}
    if launch_identity is not None:
        # The caller supplies the canonical store/track/attempt and a fresh
        # execution ID. Persist intent before exposing the bridge command.
        identity = {**launch_identity, "directory": str(launch_identity["directory"])}
        LaunchGate.prepare(**identity, backend=launcher["backend"])
        note_prepared(identity)
        spec["launch_identity"] = identity
    process = TerminalProcess(folder)
    if launch_identity is not None:
        lease = folder / "terminal-driver.lease"
        os.mkfifo(lease, 0o600)
        reader = os.open(lease, os.O_RDONLY | os.O_NONBLOCK)
        try:
            process.lease_fd = os.open(lease, os.O_WRONLY | os.O_NONBLOCK)
        finally:
            os.close(reader)
        spec["lease_path"] = str(lease)
    (folder / "terminal-spec.json").write_text(json.dumps(spec))
    bridge = str(Path(__file__).with_name("terminal_worker.py"))
    command = shlex.join([sys.executable, bridge, str(folder / "terminal-spec.json")])
    record = {**launcher, "status": "launching", "title": title}
    (folder / "launch.json").write_text(json.dumps(record))
    try:
        if launcher["backend"] == "orca":
            result = orca_result(
                launcher["cli"],
                [
                    "terminal",
                    "create",
                    "--worktree",
                    launcher["worktree"],
                    "--title",
                    title,
                    "--command",
                    command,
                ],
                launcher["repo"],
            )
            record["terminal"] = result["terminal"]
        else:
            if launcher["backend"] == "tmux":
                args = [
                    "tmux",
                    "-S",
                    launcher["socket"],
                    "new-window",
                    "-d",
                    "-P",
                    "-F",
                    "#{window_id}",
                    "-n",
                    title,
                    "-c",
                    workspace,
                    command,
                ]
            else:
                args = [
                    x.replace("{command}", command)
                    .replace("{cwd}", workspace)
                    .replace("{title}", title)
                    for x in launcher["argv"]
                ]
            result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
            record["handle"] = result.stdout.strip()
        record["status"] = "accepted"
    except BaseException:
        record["status"] = "unconfirmed"
        process.stop()
        raise
    finally:
        (folder / "launch.json").write_text(json.dumps(record))
    return process
