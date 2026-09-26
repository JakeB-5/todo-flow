"""Reserve shared capacity before a workflow dispatches a visible terminal.

Acceptance is evidence of dispatch, not authority to reuse or close a terminal.
Until physical retirement is independently confirmed, capacity remains charged,
including after process exit, driver death or an ambiguous launcher response.
"""

from pathlib import Path

from .process_inventory import ProcessInventory
from .terminal_release import retire_launch
from .terminal_slots import TerminalCapacityError, TerminalSlots
from .terminal_tmux import socket_identity


def reconcile_tmux_terminals(slots):
    """Reobserve pending automatic removals before reserving another terminal.

    Snapshot without retaining the ledger lock across retirement: retirement
    acquires the attempt inventory and launch locks before the ledger lock.
    It rechecks the current claim, execution, resource and process confirmation.
    Reserved launches remain charged and are never inferred from physical absence.
    The tmux adapter only observes; it cannot close a retained or reused window.
    """
    if not slots.path.exists():
        return
    snapshot = slots.snapshot()
    for row in snapshot["slots"].values():
        current = row["history"][-1]
        if row["backend"] != "tmux" or current["state"] not in ("quarantined", "closing"):
            continue
        launch_record = current["resource"].get("launch_record")
        if not isinstance(launch_record, str) or not launch_record:
            continue
        # retire_launch validates the launch's ledger, identity and resource.
        # Missing or changed evidence preserves capacity and a recovery reason.
        retire_launch(Path(launch_record).parent)


def reserve_terminal(launcher, identity, folder):
    # Standalone callers without a workflow identity retain their existing API.
    # run_worker always supplies the shared state directory and execution ID.
    if identity is None:
        return None
    directory = Path(identity["directory"])
    limits = launcher.get("terminal_limits", {"concurrency": 2, "idle": 1})
    slots = TerminalSlots(directory, concurrency=limits["concurrency"], idle_limit=limits["idle"])
    # Do this before acquiring the requesting attempt's inventory lock. Each
    # pending retirement acquires its own locks and rechecks durable ownership.
    reconcile_tmux_terminals(slots)
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
    if launcher["backend"] == "tmux":
        # spawn_terminal persists this launcher snapshot before new-window.
        # A missing identity never grants permission to release capacity later.
        launcher["tmux_socket_identity"] = socket_identity(launcher.get("socket"))
    return slots, lease


def accept_terminal(reservation, record, folder):
    if reservation is None:
        return None
    slots, lease = reservation
    resource = {
        "backend": record["backend"],
        "terminal": record.get("terminal"),
        "handle": record.get("handle"),
        "launch_record": str(folder / "launch.json"),
    }
    if record["backend"] == "tmux":
        resource["tmux_socket_identity"] = record.get("tmux_socket_identity")
    return slots.transition(
        lease,
        "active",
        resource=resource,
        evidence={
            "dispatch": "accepted",
            "ownership": "not verified for reuse or close",
            "launch_record": str(folder / "launch.json"),
        },
    )
