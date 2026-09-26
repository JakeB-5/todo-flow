"""Run verification in its own process group and confirm group cleanup."""

import os
import signal
import subprocess
import time


class VerificationCleanupError(Exception):
    """Stop the task when process termination cannot be confirmed."""


def group_running(pgid):
    try:
        result = subprocess.run(
            ["ps", "-axo", "pgid=,stat="], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationCleanupError("Cannot inspect verification process group") from error
    if result.returncode:
        raise VerificationCleanupError("Cannot inspect verification process group")
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == str(pgid) and not fields[1].startswith("Z"):
            return True
    return False


def stop_group(proc):
    def send(sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    # The parent exiting does not imply its descendants exited; poll the group itself.
    deadline = time.monotonic() + 0.3
    inspection_error = None
    try:
        while group_running(proc.pid) and time.monotonic() < deadline:
            proc.poll()
            time.sleep(0.02)
    except VerificationCleanupError as error:
        inspection_error = error
    finally:
        send(signal.SIGKILL)
    try:
        output = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise VerificationCleanupError(
            "Verification pipes remain live after group termination"
        ) from error
    if inspection_error:
        raise inspection_error
    deadline = time.monotonic() + 2
    while group_running(proc.pid):
        if time.monotonic() >= deadline:
            raise VerificationCleanupError("Verification process group did not stop")
        time.sleep(0.02)
    return output


def run(argv, workspace, timeout, env=None):
    proc = subprocess.Popen(
        argv,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"} if env is None else env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        stop_group(proc)
        raise
    try:
        remaining = group_running(proc.pid)
    except VerificationCleanupError:
        stop_group(proc)
        raise
    if remaining:
        stop_group(proc)
        raise RuntimeError("Verification left background processes; the group was terminated")
    if proc.returncode:
        raise RuntimeError(
            f"{argv[0]} failed ({proc.returncode}): {stderr[-3000:]} {stdout[-1000:]}"
        )
    return (stdout + stderr).strip()
