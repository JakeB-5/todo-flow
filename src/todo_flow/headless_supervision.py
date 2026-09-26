"""Supervise a headless launch without losing its handle on journal failure.

This is a conservative integration building block: physical cleanup does not
yet establish durable ownership, so every consumed launch remains blocked.
Do not use its receipts as permission to signal a PID after driver restart.
"""

from contextlib import contextmanager

from .owned_process_group import OwnedProcessGroup
from .process_launch import LaunchGate
from .verification import VerificationCleanupError


@contextmanager
def headless_process(argv, *, identity, **popen_options):
    """Yield one live handle; always clean it, including failed acknowledgement.

    Callers own any input/output files and must keep them open through this
    context. Session creation is mandatory. A supplied identity must contain
    directory, track, attempt and a fresh execution ID. The yielded handle is
    an OwnedProcessGroup: observe leader_exited(), never reap the leader before
    context exit. Use file-backed output; pipe draining belongs to the caller.
    """
    if "start_new_session" in popen_options or "process_group" in popen_options:
        raise ValueError("Headless supervision owns the process session")
    gate = LaunchGate.prepare(**identity, backend="headless")
    proc = None
    try:
        with gate.launching():
            proc = OwnedProcessGroup(argv, **popen_options)
        yield proc
    finally:
        if proc is not None:
            try:
                gate._advance(
                    gate._event(),
                    "cleaning",
                    "Headless supervisor is attempting physical cleanup",
                    {"pid": proc.pid, "permit": str(gate.path)},
                )
            finally:
                # Even a failure to persist cleaning must reach the live handle.
                try:
                    proc.stop()
                finally:
                    event = gate._event()
                    if event["state"] in ("intent", "running", "cleaning"):
                        gate._advance(
                            event,
                            "unknown",
                            "Headless cleanup lacks durable group ownership proof",
                            {"pid": proc.pid, "permit": str(gate.path)},
                        )
    # Body errors keep their original exception, but a successful body must not
    # make a proposal usable while this execution's ownership is unconfirmed.
    raise VerificationCleanupError(
        f"Headless group ownership remains unconfirmed; reconcile {gate.barrier.path}"
    )
