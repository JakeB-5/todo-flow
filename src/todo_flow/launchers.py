"""Choose a visible terminal when available; never duplicate an uncertain launch."""

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time


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
        return {"backend": "tmux"}
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
        if receipt.get("status") == "exited":
            self.returncode = receipt["returncode"]
            return self.returncode
        if not receipt and time.monotonic() - self.started > 30:
            raise RuntimeError(
                "Terminal worker start is unconfirmed; inspect launch.json before retrying"
            )
        pid = receipt.get("runner_pid")
        if pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError as error:
                raise RuntimeError("Terminal worker disappeared without an exit receipt") from error
        return None

    def wait(self, timeout=5):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("terminal worker", timeout)
            time.sleep(0.1)
        return self.returncode

    def stop(self):
        (self.folder / "terminal-cancelled").touch()
        pid = self.pid
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
                self.wait(timeout=5)
            except ProcessLookupError:
                pass
            except (subprocess.TimeoutExpired, RuntimeError):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


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
