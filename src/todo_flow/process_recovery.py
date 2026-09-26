"""Hold expired attempts that lack attributed process supervision evidence.

The caller must fence the expired claim and hold the track's execution lock
through this check and any requeue. This helper never signals stored PIDs.
A clear journal only covers recorded launches. Until an attempt-wide inventory
contract covers every spawn and delayed dispatch, automatic recovery stays held
even when every recorded execution has confirmed cleanup.
"""

import hashlib
import json

from .process_barrier import ProcessBarrier, ProcessBarrierError
from .process_inventory import ProcessInventory
from .process_launch import LaunchGate


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
    inventory = ProcessInventory(directory, track, attempt, task, generation)
    if inventory.path.exists():
        # The caller holds the track lock and fences this expired generation.
        # Seal before inspecting gates, preventing any further registration.
        value = inventory.seal()
        history = barrier.history()
        for execution, prepared in value["executions"].items():
            gate = LaunchGate(directory, track, attempt, execution)
            events = [row for row in history if row["execution"] == execution]
            if not events and not prepared:
                # Registration was durable before prepare, which itself must
                # become durable before dispatch. No delivery was authorized.
                continue
            event = gate._event()
            if event["state"] != "confirmed":
                gate.cancel_pending()  # Only succeeds for an unconsumed permit.
        recorded = {row["execution"] for row in history if row["attempt"] == attempt}
        if not recorded.issubset(value["executions"]):
            raise ProcessBarrierError("Attempt contains an uninventoried execution")
        barrier.require_clear()
        return
    barrier.require_clear()
    history = barrier.history()
    recorded = sorted({event["execution"] for event in history if event["attempt"] == attempt})
    # There is currently no attempt-wide inventory contract. Even several
    # confirmed launches cannot rule out an unrecorded headless or verification
    # spawn. Keep legacy and partially instrumented attempts blocked until that
    # contract covers every dispatch path; never infer coverage from one receipt.
    identity = [track, attempt, task, generation]
    execution = (
        "unattributed-recovery-"
        + hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("utf-8")).hexdigest()
    )
    reason = "Expired attempt lacks proof that all process launches were recorded"
    evidence = {
        "origin": "expired-claim-without-complete-launch-inventory",
        "recorded_executions": recorded,
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
