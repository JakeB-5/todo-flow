"""Reserve shared capacity before a workflow dispatches a visible terminal.

Acceptance is evidence of dispatch, not authority to reuse or close a terminal.
Until physical retirement is independently confirmed, capacity remains charged,
including after process exit, driver death or an ambiguous launcher response.
"""

from pathlib import Path

from .process_inventory import ProcessInventory
from .terminal_slots import TerminalCapacityError, TerminalSlots


def reserve_terminal(launcher, identity, folder):
    # Standalone callers without a workflow identity retain their existing API.
    # run_worker always supplies the shared state directory and execution ID.
    if identity is None:
        return None
    directory = Path(identity["directory"])
    limits = launcher.get("terminal_limits", {"concurrency": 2, "idle": 1})
    slots = TerminalSlots(
        directory, concurrency=limits["concurrency"], idle_limit=limits["idle"]
    )
    inventory = ProcessInventory(directory, identity["track"], identity["attempt"])
    evidence = {
        "launch_record": str(folder / "launch.json"),
        "process_receipt": str(folder / "terminal-process.json"),
        "reconciliation": (
            "Inspect the execution inventory, launch record and physical backend inventory. "
            "Process exit alone does not release terminal capacity."
        ),
    }
    owner = {key: identity[key] for key in ("track", "attempt", "execution")}
    if inventory.path.exists():
        with inventory.locked():
            value = inventory.read()
            if value["state"] != "open" or identity["execution"] not in value["executions"]:
                raise TerminalCapacityError("Terminal execution is not in an open inventory")
            claim = value.get("claim", {})
            if claim.get("task") is None and claim.get("generation") is None:
                # Direct adapter callers have an inventory but no scheduler claim.
                owner.update(task=identity["execution"], generation=0)
                evidence["claim_kind"] = "standalone"
            else:
                owner.update(task=claim.get("task"), generation=claim.get("generation"))
                evidence["claim_kind"] = "scheduler"
            evidence["inventory"] = str(inventory.path)
            lease = slots.reserve(owner, launcher["backend"], evidence=evidence)
    else:
        # LaunchGate also supports standalone supervisors with explicit identities.
        owner.update(task=identity["execution"], generation=0)
        evidence["claim_kind"] = "standalone"
        lease = slots.reserve(owner, launcher["backend"], evidence=evidence)
    return slots, lease


def accept_terminal(reservation, record, folder):
    if reservation is None:
        return None
    slots, lease = reservation
    return slots.transition(
        lease,
        "active",
        resource={
            "backend": record["backend"],
            "terminal": record.get("terminal"),
            "handle": record.get("handle"),
            "launch_record": str(folder / "launch.json"),
        },
        evidence={
            "dispatch": "accepted",
            "ownership": "not verified for reuse or close",
            "launch_record": str(folder / "launch.json"),
        },
    )
