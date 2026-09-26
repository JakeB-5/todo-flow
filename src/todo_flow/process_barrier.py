"""Durable process cleanup gate; receipts never authorize signalling stored PIDs.

The host supplies an existing durable directory and must call begin BEFORE spawn.
Only the process supervisor may submit confirmation evidence after proving exit
(or proving that launch cannot occur). This ledger validates attribution and
ordering, not OS ownership. It has no decision-answer or process-signal API.
A failed write means the caller must not spawn or proceed. Never delete or
truncate a journal to recover it; incomplete records intentionally block.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path


class ProcessBarrierError(RuntimeError):
    """Execution must remain blocked until cleanup evidence is reconciled."""


TRANSITIONS = {
    "intent": {"running", "cleaning", "unknown"},
    "running": {"cleaning", "unknown"},
    "cleaning": {"unknown", "confirmed"},
    "unknown": {"cleaning"},
    "confirmed": set(),
}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


class ProcessBarrier:
    def __init__(self, directory, track):
        if not _text(track):
            raise ValueError("A track identity is required")
        self.directory = Path(directory)
        self.track = track
        key = hashlib.sha256(track.encode("utf-8")).hexdigest()
        self.path = self.directory / (key + ".jsonl")

    def _validate(self, events):
        latest = {}
        for revision, event in enumerate(events, 1):
            if not isinstance(event, dict):
                raise ValueError("Invalid event")
            if (
                type(event.get("version")) is not int
                or event["version"] != 1
                or type(event.get("revision")) is not int
                or event["revision"] != revision
                or event.get("track") != self.track
                or not _text(event.get("attempt"))
                or not _text(event.get("execution"))
                or not _text(event.get("reason"))
                or not isinstance(event.get("evidence"), dict)
                or not event["evidence"]
            ):
                raise ValueError("Incomplete or misattributed event")
            execution = event["execution"]
            state = event.get("state")
            previous = latest.get(execution)
            if previous is None:
                if state != "intent" or any(
                    item["state"] != "confirmed" for item in latest.values()
                ):
                    raise ValueError("Unresolved execution or missing spawn intent")
            elif (
                event["attempt"] != previous["attempt"]
                or state not in TRANSITIONS[previous["state"]]
            ):
                raise ValueError("Invalid execution transition")
            if state == "confirmed":
                evidence = event["evidence"]
                identity = {name: event[name] for name in ("track", "attempt", "execution")}
                if (
                    evidence.get("identity") != identity
                    or evidence.get("outcome") not in ("group-exited", "not-spawned")
                    or not _text(evidence.get("proof"))
                ):
                    raise ValueError("Confirmation requires attributed exit proof")
                if evidence["outcome"] == "not-spawned" and any(
                    item["execution"] == execution and item["state"] == "running"
                    for item in events[: revision - 1]
                ):
                    raise ValueError("A running execution cannot be declared unspawned")
            latest[execution] = event
        return latest

    def _read(self, stream):
        raw = stream.read()
        if not raw or not raw.endswith(b"\n"):
            raise ValueError("Empty or interrupted journal")
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        self._validate(events)
        return events

    def history(self):
        try:
            with self.path.open("rb") as stream:
                fcntl.flock(stream, fcntl.LOCK_SH)
                return self._read(stream)
        except FileNotFoundError as error:
            if self.directory.is_dir() and not self.path.is_symlink():
                return []
            raise ProcessBarrierError(f"Cannot inspect {self.path}: {error}") from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProcessBarrierError(f"Cannot inspect {self.path}: {error}") from error

    def require_clear(self):
        latest = self._validate(self.history())
        unresolved = [item for item in latest.values() if item["state"] != "confirmed"]
        if unresolved:
            event = unresolved[0]
            raise ProcessBarrierError(
                f"Cleanup unresolved for {event['attempt']}/{event['execution']}: "
                f"{event['state']}: {event['reason']}; reconcile {self.path}"
            )

    def begin(self, attempt, execution, *, reason, evidence):
        """Persist intent before launch; execution IDs must never be reused."""
        return self._append(attempt, execution, "intent", reason, evidence, None)

    def advance(self, attempt, execution, state, *, expected_revision, reason, evidence):
        """Compare the full journal revision before recording supervisor evidence."""
        if type(expected_revision) is not int or expected_revision < 1:
            raise ProcessBarrierError("A positive expected revision is required")
        if state == "intent":
            raise ProcessBarrierError("Use begin for spawn intent")
        return self._append(attempt, execution, state, reason, evidence, expected_revision)

    def _append(self, attempt, execution, state, reason, evidence, expected):
        try:
            created = False
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(self.path, os.O_RDWR | os.O_APPEND)
            with os.fdopen(fd, "r+b", buffering=0) as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                events = [] if created else self._read(stream)
                latest = self._validate(events)
                if expected is None:
                    if execution in latest:
                        raise ValueError("Execution identity has already been used")
                elif expected != len(events) or execution not in latest:
                    raise ValueError("Stale revision or unknown execution")
                event = {
                    "version": 1,
                    "revision": len(events) + 1,
                    "track": self.track,
                    "attempt": attempt,
                    "execution": execution,
                    "state": state,
                    "reason": reason,
                    "evidence": evidence,
                }
                self._validate([*events, event])
                payload = (json.dumps(event, allow_nan=False) + "\n").encode("utf-8")
                stream.seek(0, os.SEEK_END)
                if stream.write(payload) != len(payload):
                    raise OSError("Incomplete cleanup journal write")
                os.fsync(stream.fileno())
                # Also persist the directory entry before allowing the first spawn.
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return event["revision"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProcessBarrierError(f"Cannot update {self.path}: {error}") from error
