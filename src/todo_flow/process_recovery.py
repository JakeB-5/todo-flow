"""Hold expired attempts that lack attributed process supervision evidence.

The caller must fence the expired claim and hold the track's execution lock
through this check and any requeue. This helper never signals stored PIDs.
A clear journal only covers recorded launches; callers must still journal every
spawn and prevent delayed, unrecorded dispatch before enabling automatic recovery.
"""

import hashlib
import json

from .process_barrier import ProcessBarrier, ProcessBarrierError


def require_recovery_clear(directory, track, attempt, *, task, generation):
    """Persist a compatibility hold instead of treating missing evidence as exit.

    Only call for a positively identified expired attempt. Missing or ambiguous
    attempt rows must block recovery at the caller rather than inventing an
    attempt identity. Existing corrupt/unresolved evidence is left untouched.
    """
    if (
        not isinstance(attempt, str)
        or not attempt.strip()
        or not isinstance(task, str)
        or not task.strip()
        or type(generation) is not int
        or generation < 1
    ):
        raise ProcessBarrierError("Recovery requires the exact expired claim identity")
    barrier = ProcessBarrier(directory, track)
    barrier.require_clear()
    history = barrier.history()
    if any(event["attempt"] == attempt for event in history):
        # All executions, including those from other attempts, must be clear.
        barrier.require_clear()
        return
    identity = [track, attempt, task, generation]
    execution = (
        "unattributed-recovery-"
        + hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("utf-8")).hexdigest()
    )
    reason = "Expired attempt has no attributed launch or cleanup evidence"
    evidence = {
        "origin": "expired-claim-without-process-evidence",
        "task": task,
        "generation": generation,
        "attempt": attempt,
    }
    revision = barrier.begin(attempt, execution, reason=reason, evidence=evidence)
    barrier.advance(
        attempt,
        execution,
        "unknown",
        expected_revision=revision,
        reason=reason,
        evidence=evidence,
    )
    # If either write fails, the caller must also leave the claim unrecovered.
    # An interrupted transition leaves intent, which blocks a later driver.
    barrier.require_clear()
