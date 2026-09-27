"""Fail closed when durable terminal evidence is missing from capacity accounting.

This is an admission check, never permission to close or reuse a resource. Legacy
handles lack the lease attribution needed for automatic retirement. Preserve them
and require physical reconciliation instead of guessing that process exit freed a
terminal. No migration receipt or successful scan is cached across reservations.
"""

import json
from pathlib import Path

from .process_barrier import ProcessBarrier
from .process_inventory import ProcessInventory
from .terminal_slots import TerminalCapacityError, TerminalSlots, execution_key


def recovery_error(path, ledger, reason):
    return TerminalCapacityError(
        f"Terminal evidence requires recovery: {path}: {reason}. Ledger: {ledger}. "
        "Preserve the original launch, execution and process receipts. Inspect the "
        "physical backend inventory on the recorded host/runtime; reconcile each "
        "handle with its execution and current ownership, confirm process cleanup, "
        "and obtain an attributed physical retirement receipt or restore the "
        "matching durable lease history before retrying. Process exit, cancellation "
        "and a missing handle alone do not prove physical retirement. Do not delete "
        "logs, reset the ledger, increase limits, or infer close/reuse authority."
    )


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Evidence must be an object")
    return value


def audit_terminal_evidence(slots, value):
    """Compare STATE evidence while the caller holds the capacity ledger lock.

    Do not acquire inventory, launch or journal locks here: retirement takes those
    before the ledger lock. Unlocked evidence reads can observe an interrupted
    write and conservatively refuse admission. New dispatches persist their lease
    under this same lock before publishing any launch intent, so an in-flight
    dispatch cannot become an uncharged resource between this scan and reserve.
    """
    path = slots.directory
    try:
        covered = set()
        references = {}
        for slot, row in value["slots"].items():
            for event in row["history"]:
                covered.add(execution_key(event["owner"]))
                for source in (event["resource"], event["evidence"]):
                    reference = source.get("launch_record")
                    if isinstance(reference, str) and reference:
                        record_path = Path(reference).resolve()
                        references.setdefault(record_path, []).append(
                            (row["backend"], {"slot": slot, **event})
                        )

        # Canonical STATE/attempts/* plus standalone callers' immediate folders.
        # Never recurse through STATE/worktrees or load worker proposal payloads.
        entries = list(slots.directory.iterdir())
        folders = {entry for entry in entries if entry.is_dir()}
        attempts = slots.directory / "attempts"
        if attempts in folders:
            folders.remove(attempts)
            path = attempts
            folders.update(entry for entry in attempts.iterdir() if entry.is_dir())
        folders.update(reference.parent for reference in references)
        for folder in sorted(folders):
            path = folder
            if not folder.exists():
                # The existing lease remains charged; absence is not a refund.
                continue
            names = {entry.name for entry in folder.iterdir()}
            terminal_files = names & {"terminal-spec.json", "terminal-process.json"}
            if "launch.json" not in names and not terminal_files:
                continue
            launch_path = (folder / "launch.json").resolve()
            candidates = references.get(launch_path, [])
            if "launch.json" in names:
                path = launch_path
                launch = read_object(path)
                if launch.get("backend") == "headless":
                    if terminal_files or candidates:
                        raise ValueError("Headless record conflicts with terminal evidence")
                    continue
                lease = launch.get("terminal_slot")
                ledger = launch.get("terminal_ledger")
                if (
                    not isinstance(ledger, str)
                    or Path(ledger).resolve() != slots.path.resolve()
                    or not any(
                        backend == launch.get("backend") and event == lease
                        for backend, event in candidates
                    )
                ):
                    raise ValueError("Visible launch has no matching attributed lease history")
            elif not candidates:
                path = folder / sorted(terminal_files)[0]
                raise ValueError("Orphan terminal execution evidence has no capacity lease")
            if "terminal-spec.json" in names:
                path = folder / "terminal-spec.json"
                identity = read_object(path).get("launch_identity")
                if (
                    not isinstance(identity, dict)
                    or Path(identity["directory"]).resolve() != slots.directory.resolve()
                    or not any(
                        execution_key(identity) == execution_key(event["owner"])
                        for _, event in candidates
                    )
                ):
                    raise ValueError("Terminal specification has no matching execution lease")
            if "terminal-process.json" in names:
                path = folder / "terminal-process.json"
                # Validate readability, but never use returncode/cleanup_confirmed
                # to release capacity or excuse an unaccounted launch.
                read_object(path)

        nonterminal = set()
        for path in sorted(entries):
            if path.suffix != ".jsonl" or len(path.stem) != 64:
                continue
            if any(character not in "0123456789abcdef" for character in path.stem):
                continue
            raw = path.read_bytes()
            if not raw or not raw.endswith(b"\n"):
                raise ValueError("Empty or interrupted execution journal")
            events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
            barrier = ProcessBarrier(slots.directory, events[0]["track"])
            if barrier.path != path:
                raise ValueError("Misattributed execution journal")
            barrier._validate(events)
            for event in events:
                if event["state"] != "intent":
                    continue
                key = execution_key(event)
                backend = event["evidence"].get("backend")
                if backend in ("headless", "supervised"):
                    nonterminal.add(key)
                elif key not in covered:
                    raise ValueError(
                        "Visible or unknown execution intent has no capacity lease: "
                        + "/".join(key)
                    )

        for path in sorted(entries):
            if not path.name.endswith(".inventory.json"):
                continue
            identity = read_object(path)["identity"]
            inventory = ProcessInventory(slots.directory, identity["track"], identity["attempt"])
            if inventory.path != path:
                raise ValueError("Misattributed process inventory")
            recorded = inventory.read()
            for execution, prepared in recorded["executions"].items():
                key = (identity["track"], identity["attempt"], execution)
                if prepared and key not in covered and key not in nonterminal:
                    raise ValueError("Prepared execution lacks terminal or headless attribution")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        raise recovery_error(path, slots.path, str(error)) from error


class AuditedTerminalSlots(TerminalSlots):
    def check_execution(self, value, owner):
        # reserve/checkout invoke this inside their ledger critical section, before
        # checking capacity or writing a reservation. Keep the established outer
        # inventory -> ledger lock order and recheck on every admission attempt.
        audit_terminal_evidence(self, value)
        super().check_execution(value, owner)
