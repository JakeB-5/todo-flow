"""Own one command from a separate, surviving supervisor process.

The driver must prepare LaunchGate before dispatch, pass only the read end of a
private pipe here, and retain its sole write end until it requests cleanup.
EOF (including driver SIGKILL) requests cleanup without trusting a stored PID.
Run this entrypoint in a new session, with file-backed standard streams.
This per-execution receipt is NOT an inventory or an attempt recovery receipt.
"""

import fcntl
import json
import os
from pathlib import Path
import select
import signal
import stat
import sys
import time

if __package__:
    from .owned_process_group import OwnedProcessGroup
    from .process_barrier import ProcessBarrierError
    from .process_launch import LaunchGate
    from .verification import VerificationCleanupError
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from todo_flow.owned_process_group import OwnedProcessGroup
    from todo_flow.process_barrier import ProcessBarrierError
    from todo_flow.process_launch import LaunchGate
    from todo_flow.verification import VerificationCleanupError


def _finish(gate, owner, outcome):
    """Retain the live pin through retry; only this owner can attest group exit."""
    details = {
        "pid": owner.pid,
        "supervisor_pid": os.getpid(),
        "permit": str(gate.path),
        "completion": outcome,
    }
    while True:
        journal_error = None
        try:
            event = gate._event()
            if event["state"] != "cleaning":
                gate._advance(event, "cleaning", "Surviving supervisor is cleaning", details)
        except (OSError, ProcessBarrierError) as error:
            journal_error = error
        try:
            code = owner.stop()
        except VerificationCleanupError as error:
            # Recording a failure must not discard the only live ownership pin.
            try:
                event = gate._event()
                if event["state"] in ("intent", "running", "cleaning"):
                    gate._advance(
                        event,
                        "unknown",
                        "Cleanup failed; the same supervisor retains ownership and will retry",
                        {**details, "error": str(error)},
                    )
            except (OSError, ProcessBarrierError):
                pass
            time.sleep(0.1)
            continue
        if journal_error is not None:
            # Physical cleanup still ran. A journal failure cannot be converted
            # into success or reconstructed by a future driver from this PID.
            raise journal_error
        gate._advance(
            gate._event(),
            "confirmed",
            "Owning supervisor confirmed group exit before reaping its leader",
            {
                **details,
                "identity": {
                    "track": gate.barrier.track,
                    "attempt": gate.attempt,
                    "execution": gate.execution,
                },
                "outcome": "group-exited",
                "proof": "Original live owner retained the unreaped leader until no live members",
                "returncode": code,
            },
        )
        return code


def supervise(argv, *, identity, lease_fd, cwd, timeout):
    """Consume a pre-dispatch permit once and supervise until cleanup is proven.

    Inherited standard streams must be files, not pipes requiring a driver to
    drain them. The lease is never inherited by the command. No fallback spawn
    or PID-based recovery is supported. Supervisor death leaves the gate closed.
    """
    if os.getsid(0) != os.getpid():
        raise VerificationCleanupError("Supervisor requires a separate session")
    if type(lease_fd) is not int or lease_fd < 3:
        raise ValueError("Lease must use a dedicated descriptor, not a standard stream")
    if (
        not stat.S_ISFIFO(os.fstat(lease_fd).st_mode)
        or fcntl.fcntl(lease_fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
    ):
        raise ValueError("Supervisor requires the read end of a private lease pipe")
    if timeout <= 0:
        raise ValueError("Supervisor timeout must be positive")
    os.set_inheritable(lease_fd, False)
    gate = LaunchGate(**identity)
    requested = False

    def request_stop(signum, frame):
        nonlocal requested
        requested = True

    handlers = {
        sig: signal.signal(sig, request_stop)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    owner = None
    outcome = "supervisor-error"
    try:
        if select.select([lease_fd], [], [], 0)[0] or requested:
            gate.cancel_pending()
            return 125
        try:
            with gate.launching():
                owner = OwnedProcessGroup(argv, cwd=cwd, close_fds=True)
            deadline = time.monotonic() + timeout
            while True:
                if requested:
                    outcome = "signal"
                    break
                if select.select([lease_fd], [], [], 0.02)[0]:
                    outcome = "driver-disconnected"
                    break
                if owner.leader_exited():
                    outcome = (
                        "leader-exited-with-descendants"
                        if owner._snapshot()[1]
                        else "leader-exited"
                    )
                    break
                if time.monotonic() >= deadline:
                    outcome = "timeout"
                    break
        finally:
            if owner is not None:
                code = _finish(gate, owner, outcome)
        if outcome.startswith("leader-exited"):
            return code if code >= 0 else 128 - code
        return 124 if outcome == "timeout" else 125
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        os.close(lease_fd)


if __name__ == "__main__":
    specification = json.loads(sys.argv[1])
    raise SystemExit(supervise(**specification))
