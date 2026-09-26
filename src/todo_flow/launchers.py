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
    if not payload.get("ok"):
        raise RuntimeError("Orca could not resolve this project terminal context")
    return payload["result"]


def select_launcher(config, workspace):
    mode = config.get("worker_launcher", "auto")
    if mode == "headless":
        return {"backend": "headless"}
    if mode not in ("auto", "orca", "tmux", "terminal"):
        raise ValueError("Unknown worker_launcher: " + str(mode))
    repo = str(Path(config.get("repo", workspace)).resolve())
    if mode in ("auto", "orca"):
        cli = orca_command()
        if shutil.which(cli):
            try:
                status = orca_result(cli, ["status"], repo)
                if status["app"]["running"] and status["runtime"]["reachable"]:
                    result = orca_result(
                        cli, ["worktree", "show", "--worktree", "path:" + repo], repo
                    )
                    worktree = result["worktree"]
                    # The bridge and its files must live on the same host as the PTY.
                    if worktree.get("hostId", "local") != "local":
                        raise RuntimeError("Remote Orca terminals require a host-local driver")
                    return {
                        "backend": "orca",
                        "cli": cli,
                        "worktree": "id:" + worktree["id"],
                        "repo": repo,
                    }
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                KeyError,
                RuntimeError,
            ) as error:
                if mode == "orca":
                    raise RuntimeError(f"Orca terminal unavailable: {error}") from error
        if mode == "orca":
            raise RuntimeError(
                "Orca terminal unavailable; start Orca or select headless explicitly"
            )
    if mode in ("auto", "terminal") and config.get("terminal_command"):
        argv = config["terminal_command"]
        if (
            not isinstance(argv, list)
            or not all(isinstance(x, str) for x in argv)
            or not any("{command}" in x for x in argv)
        ):
            raise ValueError("terminal_command must be an argv array containing {command}")
        return {"backend": "terminal", "argv": argv}
    if mode in ("auto", "tmux") and os.environ.get("TMUX") and shutil.which("tmux"):
        return {"backend": "tmux", "socket": os.environ["TMUX"].rsplit(",", 2)[0]}
    if mode != "auto":
        raise RuntimeError(f"{mode} terminal unavailable; configure a launcher or select headless")
    return {"backend": "headless"}


class TerminalProcess:
    def __init__(self, folder):
        self.folder = folder
        self.started = time.monotonic()
        self.returncode = None

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
        # Only the bridge owns a current Popen object. A persisted PID is not
        # authority to signal a process, even when that PID currently exists.
        (self.folder / "terminal-cancelled").touch()
        deadline = time.monotonic() + timeout
        while True:
            if self.poll() is not None:
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


def spawn_terminal(launcher, argv, workspace, folder, title):
    argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    spec = {"argv": argv, "cwd": workspace, "title": title}
    (folder / "terminal-spec.json").write_text(json.dumps(spec))
    bridge = str(Path(__file__).with_name("terminal_worker.py"))
    command = shlex.join([sys.executable, bridge, str(folder / "terminal-spec.json")])
    record = {**launcher, "status": "launching", "title": title}
    (folder / "launch.json").write_text(json.dumps(record))
    process = TerminalProcess(folder)
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
    except Exception:
        record["status"] = "unconfirmed"
        process.stop()
        raise
    finally:
        (folder / "launch.json").write_text(json.dumps(record))
    return process
