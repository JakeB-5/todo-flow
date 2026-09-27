"""Retire one owned terminal after process exit; never gate another launch."""

from dataclasses import dataclass
import json

from .maintenance import write_json
from .process_inventory import ProcessInventory
from .process_launch import LaunchGate


class TerminalRetirementError(RuntimeError):
    """Preserve a terminal whose ownership or exit cannot be confirmed."""


@dataclass(frozen=True)
class TerminalObservation:
    status: str
    resource: dict
    proof: str
    activity_token: str = ""


def inspect_checked(inspect_resource, resource):
    observation = inspect_resource(resource)
    if (
        not isinstance(observation, TerminalObservation)
        or observation.status not in ("absent", "idle", "busy", "unknown", "pending")
        or observation.resource != resource
        or not isinstance(observation.proof, str)
        or not observation.proof.strip()
        or (observation.status == "idle" and not observation.activity_token)
    ):
        raise TerminalRetirementError(
            "Physical terminal observation is incomplete or misattributed"
        )
    return observation


def retire_terminal(
    folder,
    owner,
    resource,
    directory,
    *,
    inspect_resource,
    close_resource,
    prior_close=False,
):
    """Check only this execution. Preserve uncertain UI cleanup without blocking work.

    Inventory and launch locks protect the original claim and process evidence.
    A durable close intent prevents replay after response loss. No capacity ledger
    or other attempt's history participates in this operation.
    """
    inventory = ProcessInventory(
        directory, owner["track"], owner["attempt"], owner.get("task"), owner.get("generation")
    )
    gate = LaunchGate(directory, owner["track"], owner["attempt"], owner["execution"])
    with inventory.locked():
        recorded = inventory.read()
        if recorded["executions"].get(owner["execution"]) is not True:
            raise TerminalRetirementError("Terminal execution lacks prepared inventory evidence")
        with gate._locked():
            if gate._event()["state"] != "confirmed":
                raise TerminalRetirementError("Process cleanup is not confirmed for this execution")
            report = {
                "attempt": folder.name,
                "backend": resource["backend"],
                "owner": owner,
                "resource": resource,
                "status": "preserved",
            }
            previous_path = folder / "terminal-retirement.json"
            if previous_path.exists():
                previous = json.loads(previous_path.read_text())
                if (
                    previous.get("status") == "closed"
                    and previous.get("owner") == owner
                    and previous.get("resource") == resource
                ):
                    return previous
            observation = inspect_checked(inspect_resource, resource)
            report["evidence"] = {
                "process_confirmation": str(gate.barrier.path),
                "physical_status": observation.status,
                "physical_proof": observation.proof,
            }
            if observation.status == "absent":
                return {**report, "status": "closed"}
            if observation.status != "idle":
                return {**report, "reason": "Terminal is still active, reused, or unconfirmed"}
            intent = folder / "terminal-close-intent.json"
            if prior_close or intent.exists():
                return {
                    **report,
                    "reason": "Close was already requested; physical absence is unconfirmed",
                }
            write_json(
                intent, {"owner": owner, "resource": resource, "evidence": report["evidence"]}
            )
            close_resource(observation)
            after = inspect_checked(inspect_resource, resource)
            if after.status == "absent":
                return {
                    **report,
                    "status": "closed",
                    "evidence": {
                        **report["evidence"],
                        "physical_status": "absent",
                        "physical_proof": after.proof,
                    },
                }
            return {**report, "reason": "Physical terminal removal is unconfirmed"}
