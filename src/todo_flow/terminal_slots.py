"""Durable terminal capacity accounting for trusted lifecycle supervisors.

This ledger grants capacity, never permission to send input or close a terminal.
Callers must independently check ProcessInventory, LaunchGate, physical identity,
activity and group exit before recording a transition. Persist the reservation
before creating a terminal and the closing transition before issuing a close.
An interrupted operation remains charged until independent evidence resolves it.

Backend integration is intentionally separate: no subprocesses run here.
"""

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import uuid


class TerminalCapacityError(RuntimeError):
    """Capacity or ownership requires reconciliation before another operation."""


STATES = {"reserved", "active", "idle", "closing", "quarantined", "closed"}
TRANSITIONS = {
    "reserved": {"active", "quarantined"},
    "active": {"idle", "closing", "quarantined"},
    "idle": {"closing", "quarantined"},
    "closing": {"closed", "quarantined"},
    "quarantined": {"closing"},
    "closed": set(),
}


def validate_owner(owner):
    if not isinstance(owner, dict):
        raise ValueError("Terminal owner must be an object")
    for key in ("track", "attempt", "execution", "task"):
        if not isinstance(owner.get(key), str) or not owner[key].strip():
            raise ValueError("Terminal owner requires " + key)
    if type(owner.get("generation")) is not int or owner["generation"] < 0:
        raise ValueError("Terminal owner requires a nonnegative claim generation")


def execution_key(owner):
    return tuple(owner[key] for key in ("track", "attempt", "execution"))


class TerminalSlots:
    def __init__(self, directory, *, concurrency, idle_limit):
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError("Terminal concurrency must be positive")
        if type(idle_limit) is not int or idle_limit < 0:
            raise ValueError("Terminal idle limit must be nonnegative")
        self.directory = Path(directory)
        self.path = self.directory / "terminal-slots.json"
        self.lock = self.directory / "terminal-slots.lock"
        self.limits = {"concurrency": concurrency, "idle": idle_limit}

    @contextmanager
    def locked(self):
        # The host creates the state directory. Never replace the lock inode.
        with self.lock.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self):
        if not self.path.exists():
            return {"version": 1, "limits": self.limits, "slots": {}, "max_owned": 0}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                type(value.get("version")) is not int
                or value["version"] != 1
                or value.get("limits") != self.limits
                or not isinstance(value.get("slots"), dict)
                or type(value.get("max_owned")) is not int
                or value["max_owned"] < 0
            ):
                raise ValueError("Unsupported ledger or changed capacity limits")
            for slot, row in value["slots"].items():
                if not isinstance(slot, str) or not slot:
                    raise ValueError("Invalid slot identity")
                if not isinstance(row, dict) or not row.get("history"):
                    raise ValueError("Missing slot history")
                if not isinstance(row["history"], list):
                    raise ValueError("Invalid slot history")
                if not isinstance(row.get("backend"), str) or not row["backend"]:
                    raise ValueError("Missing backend identity")
                for revision, event in enumerate(row["history"], 1):
                    validate_owner(event["owner"])
                    if (
                        type(event.get("revision")) is not int
                        or event["revision"] != revision
                        or event.get("state") not in STATES
                        or not isinstance(event.get("evidence"), dict)
                        or not event["evidence"]
                        or not isinstance(event.get("resource"), dict)
                    ):
                        raise ValueError("Invalid terminal event")
            return value
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise TerminalCapacityError(
                f"Cannot reconcile terminal capacity; inspect {self.path}: {error}"
            ) from error

    def write(self, value):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def counts(self, value):
        counts = dict.fromkeys(STATES, 0)
        for row in value["slots"].values():
            counts[row["history"][-1]["state"]] += 1
        return counts

    def check_execution(self, value, owner):
        for row in value["slots"].values():
            for event in row["history"]:
                if execution_key(event["owner"]) == execution_key(owner):
                    raise TerminalCapacityError(
                        f"Execution already has terminal evidence; inspect {self.path}"
                    )

    def check_active_capacity(self, value):
        counts = self.counts(value)
        # Unknown resources might still be running. Do not assume they are idle.
        potential = sum(counts[state] for state in ("reserved", "active", "quarantined"))
        if potential >= self.limits["concurrency"]:
            raise TerminalCapacityError(
                f"Terminal concurrency is occupied or uncertain; reconcile {self.path}"
            )

    def append(self, value, slot, state, owner, resource, evidence):
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("A durable evidence reference is required")
        if not isinstance(resource, dict):
            raise ValueError("Resource identity must be an object")
        history = value["slots"][slot]["history"]
        event = {
            "revision": len(history) + 1,
            "state": state,
            "owner": copy.deepcopy(owner),
            "resource": copy.deepcopy(resource),
            "evidence": copy.deepcopy(evidence),
        }
        history.append(event)
        counts = self.counts(value)
        owned = sum(counts.values()) - counts["closed"]
        value["max_owned"] = max(value["max_owned"], owned)
        self.write(value)
        return {"slot": slot, **copy.deepcopy(event)}

    def reserve(self, owner, backend, *, evidence):
        validate_owner(owner)
        if not isinstance(backend, str) or not backend or backend == "headless":
            raise ValueError("Only a visible terminal backend may reserve a slot")
        with self.locked():
            value = self.read()
            self.check_execution(value, owner)
            self.check_active_capacity(value)
            counts = self.counts(value)
            owned = sum(counts.values()) - counts["closed"]
            if owned >= self.limits["concurrency"] + self.limits["idle"]:
                raise TerminalCapacityError(
                    f"Terminal slots are occupied; reconcile or retire an idle slot in {self.path}"
                )
            slot = uuid.uuid4().hex
            value["slots"][slot] = {"backend": backend, "history": []}
            return self.append(value, slot, "reserved", owner, {}, evidence)

    def current(self, value, lease):
        try:
            row = value["slots"][lease["slot"]]
            event = row["history"][-1]
            if lease != {"slot": lease["slot"], **event}:
                raise ValueError("Lease was superseded or altered")
            return event
        except (KeyError, TypeError, ValueError) as error:
            raise TerminalCapacityError("Stale terminal lease; reconcile before acting") from error

    def transition(self, lease, state, *, evidence, resource=None):
        """Record independently verified facts; this method performs no backend action.

        A closing lease may authorize one close dispatch by its caller. On restart,
        inspect the backend instead of replaying that dispatch. Quarantine never
        frees capacity and cannot be changed directly to idle or closed.
        """
        with self.locked():
            value = self.read()
            event = self.current(value, lease)
            if state not in TRANSITIONS[event["state"]]:
                raise TerminalCapacityError("Invalid terminal lifecycle transition")
            if state == "idle" and self.counts(value)["idle"] >= self.limits["idle"]:
                raise TerminalCapacityError("Idle capacity is full; retire the owned terminal")
            identity = event["resource"] if resource is None else resource
            if event["state"] != "reserved" and identity != event["resource"]:
                raise TerminalCapacityError("Physical identity changed; quarantine the terminal")
            if state == "active" and not identity:
                raise ValueError("Accepted terminal requires physical identity")
            return self.append(value, lease["slot"], state, event["owner"], identity, evidence)

    def checkout(self, lease, owner, *, evidence):
        """Reserve a verified idle resource for a fresh execution, before dispatch.

        The caller must establish safe reuse capability and unchanged ownership
        and activity immediately before using the returned reservation.
        """
        validate_owner(owner)
        with self.locked():
            value = self.read()
            event = self.current(value, lease)
            if event["state"] != "idle":
                raise TerminalCapacityError("Only confirmed idle terminals can be reused")
            self.check_execution(value, owner)
            self.check_active_capacity(value)
            return self.append(value, lease["slot"], "reserved", owner, event["resource"], evidence)

    def snapshot(self):
        with self.locked():
            value = self.read()
            return copy.deepcopy(value)
