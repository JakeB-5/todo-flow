"""Run verification in its own process group and confirm group cleanup."""

import os
import signal
import subprocess
import tempfile
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


def check_leader(proc):
    """Reject observable identity mismatches before inspecting or signalling.

    The caller must retain the original Popen object for a dedicated session.
    A PID that exists after that object has reaped its child belongs to another
    execution. This check is deliberately not a recovery ownership proof: an
    absent leader cannot distinguish orphaned descendants from a reused group
    whose replacement leader has also exited.
    """
    try:
        pgid = os.getpgid(proc.pid)
        sid = os.getsid(proc.pid)
    except ProcessLookupError:
        # The original leader may have exited, leaving live descendants.
        return
    except OSError as error:
        raise VerificationCleanupError(
            f"Cannot inspect process identity for group {proc.pid}"
        ) from error
    if proc.returncode is not None:
        raise VerificationCleanupError(
            f"Process identity mismatch: reaped PID {proc.pid} exists again"
        )
    if pgid != proc.pid or sid != proc.pid:
        raise VerificationCleanupError(
            f"Process identity mismatch: PID {proc.pid} is not its own session/group leader"
        )


def stop_group(proc, *, collect_output=True):
    """Reap before inspecting and signal only while live group members remain.

    This helper accepts the current driver's Popen object. It must not be used
    with a PID reconstructed from an old receipt as proof of ownership.
    Callers with dedicated pipe readers must disable output collection and
    separately bound and check their readers after this function returns.
    """

    def running():
        # Reap a terminated direct child before querying or signalling the group.
        # In particular, macOS can reject signals to a zombie-only group.
        proc.poll()
        check_leader(proc)
        return group_running(proc.pid)

    def send(sig):
        if not running():
            return
        # Group inspection invokes ps. Check again after that inspection so a
        # mismatch discovered during escalation cannot authorize another signal.
        check_leader(proc)
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
        if collect_output:
            output = proc.communicate(timeout=5)
        else:
            proc.wait(timeout=5)
            output = None
    except subprocess.TimeoutExpired as error:
        raise VerificationCleanupError(
            "Verification process or pipes remain live after group termination"
        ) from error
    except OSError as error:
        raise VerificationCleanupError("Cannot collect verification process output") from error
    if running():
        raise VerificationCleanupError("Verification process group did not stop")
    return output


def run_supervised(argv, workspace, timeout, identity):
    # Imported lazily because terminal_worker also imports this file directly.
    from .owned_process_group import OwnedProcessGroup
    from .process_launch import LaunchGate

    gate = LaunchGate.prepare(**identity, backend="verification")
    proc = None
    # Files cannot hold a reader waiting for EOF after the leader exits. Keep
    # both open through cleanup, including failed durable acknowledgement.
    with tempfile.TemporaryFile(mode="w+t") as stdout, tempfile.TemporaryFile(mode="w+t") as stderr:
        try:
            with gate.launching():
                proc = OwnedProcessGroup(
                    argv,
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
            deadline = time.monotonic() + timeout
            while not proc.leader_exited():
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.02)
        finally:
            if proc is not None:
                try:
                    gate._advance(
                        gate._event(),
                        "cleaning",
                        "Verification supervisor is attempting physical cleanup",
                        {"pid": proc.pid},
                    )
                finally:
                    # Never reap before group cleanup or bypass the live handle
                    # when a journal write fails.
                    try:
                        code = proc.stop()
                    finally:
                        event = gate._event()
                        if event["state"] in ("intent", "running", "cleaning"):
                            gate._advance(
                                event,
                                "unknown",
                                "Verification cleanup lacks durable group ownership proof",
                                {"pid": proc.pid, "permit": str(gate.path)},
                            )
        if code:
            stdout.seek(0)
            stderr.seek(0)
            raise RuntimeError(
                f"{argv[0]} failed ({code}): {stderr.read()[-3000:]} {stdout.read()[-1000:]}"
            )
    # Live-driver ownership is not a durable driver-death recovery contract.
    raise VerificationCleanupError(
        f"Verification group ownership remains unconfirmed; reconcile {gate.barrier.path}"
    )


def run(argv, workspace, timeout, *, launch_identity=None):
    if launch_identity is not None:
        return run_supervised(argv, workspace, timeout, launch_identity)
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
        check_leader(proc)
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
