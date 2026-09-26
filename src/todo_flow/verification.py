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
    if result.returncode or not result.stdout.strip():
        raise VerificationCleanupError("Cannot inspect verification process group")
    running = False
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise VerificationCleanupError("Invalid verification process group inspection")
        if fields[0] == str(pgid) and not fields[1].startswith("Z"):
            running = True
    return running


def stop_group(proc):
    """Reap before inspecting and signal only while live group members remain.

    This helper accepts the current driver's Popen object. It must not be used
    with a PID reconstructed from an old receipt as proof of ownership.
    """

    def running():
        # Reap a terminated direct child before querying or signalling the group.
        # In particular, macOS can reject signals to a zombie-only group.
        proc.poll()
        return group_running(proc.pid)

    def send(sig):
        if not running():
            return
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            # A concurrent exit is expected, but still requires final inspection.
            pass
        except PermissionError as error:
            # A process can exit between inspection and the signal. Confirm that
            # case after reaping; never suppress a denial for a live group.
            if running():
                raise VerificationCleanupError(
                    f"Cannot send {sig.name} to verification process group {proc.pid}"
                ) from error
        except OSError as error:
            raise VerificationCleanupError(
                f"Cannot send {sig.name} to verification process group {proc.pid}"
            ) from error

    def wait_for_exit(timeout):
        deadline = time.monotonic() + timeout
        while running():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True

    send(signal.SIGTERM)
    if not wait_for_exit(0.3):
        send(signal.SIGKILL)
        if not wait_for_exit(2):
            raise VerificationCleanupError("Verification process group did not stop")
    try:
        output = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise VerificationCleanupError(
            "Verification pipes remain live after group termination"
        ) from error
    except OSError as error:
        raise VerificationCleanupError("Cannot collect verification process output") from error
    if running():
        raise VerificationCleanupError("Verification process group did not stop")
    return output


def run(argv, workspace, timeout):
    proc = subprocess.Popen(
        argv,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
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
