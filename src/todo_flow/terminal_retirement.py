"""Serialize terminal retirement after independently confirmed process cleanup.

Callbacks are trusted host adapters, never worker proposals. inspect_resource
must attribute physical observations to the original resource and runtime.
An idle observation requires verified identity, activity and execution exit.
The Orca policy decision-95af607c8a6244a8 permits input racing after inspection;
it does not waive any pre-close check or require an atomic backend API.

A pending observation is a delayed exit, not permission to close. It retains
capacity and permits later inspection. Busy or unknown resources quarantine;
only verified physical absence releases capacity.
"""

from dataclasses import dataclass

from .process_inventory import ProcessInventory
from .process_launch import LaunchGate
from .terminal_slots import TerminalCapacityError


@dataclass(frozen=True)
class TerminalObservation:
    status: str
    resource: dict
    proof: str
    activity_token: str = ""


def retirement_evidence(gate, observation):
    return {
        "process_confirmation": str(gate.barrier.path),
        "physical_status": observation.status,
        "physical_proof": observation.proof,
        "activity_token": observation.activity_token,
    }


def inspect_checked(inspect_resource, resource):
    observation = inspect_resource(resource)
    if (
        not isinstance(observation, TerminalObservation)
        or observation.status not in ("absent", "idle", "busy", "unknown", "pending")
        or observation.resource != resource
        or not isinstance(observation.proof, str)
        or not observation.proof.strip()
        or not isinstance(observation.activity_token, str)
        or (observation.status == "idle" and not observation.activity_token.strip())
    ):
        raise TerminalCapacityError("Physical terminal observation is incomplete or misattributed")
    return observation


def retire_terminal(slots, lease, *, inspect_resource, close_resource):
    """Return the latest lease; only physical absence releases its capacity.

    The supplied lease identifies a specific execution and resource. A previous
    invocation may already have advanced its revision; only the ledger's current
    revision is used for transitions. The inventory lock serializes all cleanup
    callers for this attempt and fences claim replacement during inspection and
    close. The launch lock fences supervisor transitions during retirement.

    Once closing is durable, recovery only observes. It never retries a close
    whose response may have been lost. A refusal, exception or crash therefore
    leaves capacity charged. Exceptions propagate so the caller can retain a
    recovery reason without treating terminal retirement as confirmed.
    """
    owner = lease["owner"]
    inventory = ProcessInventory(
        slots.directory,
        owner["track"],
        owner["attempt"],
        owner["task"],
        owner["generation"],
    )
    gate = LaunchGate(slots.directory, owner["track"], owner["attempt"], owner["execution"])
    with inventory.locked():
        recorded = inventory.read()
        if recorded["executions"].get(owner["execution"]) is not True:
            raise TerminalCapacityError("Terminal execution lacks prepared inventory evidence")
        with gate._locked():
            event = gate._event()
            if event["state"] != "confirmed":
                raise TerminalCapacityError("Process cleanup is not confirmed for this execution")
            snapshot = slots.snapshot()
            try:
                row = snapshot["slots"][lease["slot"]]
                current = {"slot": lease["slot"], **row["history"][-1]}
                if (
                    current["owner"] != owner
                    or current["resource"] != lease["resource"]
                    or row["backend"] != lease["resource"].get("backend")
                ):
                    raise ValueError("Terminal ownership or physical identity changed")
            except (KeyError, TypeError, ValueError) as error:
                raise TerminalCapacityError("Cannot reconcile terminal retirement lease") from error
            if current["state"] == "closed":
                return current
            if current["state"] == "reserved":
                raise TerminalCapacityError("Unaccepted dispatch requires launch reconciliation")
            observation = inspect_checked(inspect_resource, current["resource"])
            evidence = retirement_evidence(gate, observation)
            if observation.status == "absent":
                if current["state"] != "closing":
                    current = slots.transition(current, "closing", evidence=evidence)
                return slots.transition(current, "closed", evidence=evidence)
            # A durable close intent permits at most one dispatch. An old
            # quarantine also needs explicit recovery rather than automatic retry.
            if current["state"] in ("closing", "quarantined"):
                return current
            if observation.status == "pending":
                return current
            if observation.status != "idle":
                return slots.transition(current, "quarantined", evidence=evidence)
            current = slots.transition(current, "closing", evidence=evidence)
            # No code above this point may issue a close. Backend-specific
            # preconditions were inspected under the ownership locks above.
            close_resource(observation)
            after = inspect_checked(inspect_resource, current["resource"])
            if after.status == "absent":
                return slots.transition(
                    current, "closed", evidence=retirement_evidence(gate, after)
                )
            return current
