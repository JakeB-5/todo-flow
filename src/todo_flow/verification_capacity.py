"""Project-wide admission for host verification commands.

The stable lock serializes queue updates, not process lifetimes. A dead driver
does not return a slot: only attributed cleanup, or a fenced inventory proving
no dispatch, does. Keep released requests so waits remain inspectable.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

from .process_barrier import ProcessBarrier, ProcessBarrierError
from .process_inventory import ProcessInventory


class VerificationCapacity:
    def __init__(self, directory, limit):
        if type(limit) is not int or limit < 1:
            raise ValueError("verify_concurrency must be a positive integer")
        self.directory = Path(directory)
        self.limit = limit
        self.path = self.directory / "verification-capacity.json"
        self.lock = self.directory / "verification-capacity.lock"

    @contextmanager
    def locked(self):
        # Never replace/unlink this inode; independent drivers share it.
        with self.lock.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self):
        try:
            if not self.path.exists():
                return {"version": 1, "limit": self.limit, "requests": []}
            value = json.loads(self.path.read_text())
            if (
                type(value.get("version")) is not int
                or value["version"] != 1
                or type(value.get("limit")) is not int
                or value["limit"] != self.limit
                or not isinstance(value.get("requests"), list)
            ):
                raise ValueError("Unsupported queue or conflicting verification limit")
            seen = set()
            for row in value["requests"]:
                if (
                    not isinstance(row, dict)
                    or any(
                        not isinstance(row.get(key), str) or not row[key]
                        for key in ("track", "attempt", "execution", "task", "wait_reason")
                    )
                    or type(row.get("generation")) is not int
                    or row["generation"] < 1
                    or row.get("state") not in ("waiting", "admitted", "released")
                    or row["execution"] in seen
                ):
                    raise ValueError("Invalid verification request")
                if row["state"] == "released":
                    proof = row.get("release_evidence")
                    if (
                        not isinstance(proof, dict)
                        or proof.get("identity")
                        != {key: row[key] for key in ("track", "attempt", "execution")}
                        or proof.get("outcome") not in ("group-exited", "not-spawned")
                        or not isinstance(proof.get("proof"), str)
                        or not proof["proof"]
                    ):
                        raise ValueError("Released verification lacks attributed evidence")
                seen.add(row["execution"])
            return value
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ProcessBarrierError(f"Cannot inspect {self.path}: {error}") from error

    def write(self, value):
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w") as stream:
                json.dump(value, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise ProcessBarrierError(f"Cannot persist {self.path}: {error}") from error

    def proof(self, row, *, returned=False):
        inventory = ProcessInventory(
            self.directory, row["track"], row["attempt"], row["task"], row["generation"]
        )
        value = inventory.read()
        if row["execution"] not in value["executions"]:
            raise ProcessBarrierError("Verification execution is absent from its inventory")
        barrier = ProcessBarrier(self.directory, row["track"])
        events = [event for event in barrier.history() if event["execution"] == row["execution"]]
        if events:
            event = events[-1]
            if event["attempt"] != row["attempt"]:
                raise ProcessBarrierError("Verification cleanup belongs to another attempt")
            if event["state"] == "confirmed":
                return {
                    "journal": str(barrier.path),
                    "revision": event["revision"],
                    **event["evidence"],
                }
            return None
        if value["executions"][row["execution"]]:
            raise ProcessBarrierError("Prepared verification has no launch journal")
        # Recovery seals the exact inventory under the execution/claim fence.
        # A live caller may also prove that its synchronous launch boundary
        # returned without ever preparing an intent. Neither case uses a PID.
        if returned or value["state"] == "sealed":
            return {
                "identity": {key: row[key] for key in ("track", "attempt", "execution")},
                "inventory": str(inventory.path),
                "outcome": "not-spawned",
                "proof": "Synchronous caller returned before launch intent"
                if returned
                else "Fenced inventory sealed before launch intent",
            }
        return None

    def refresh(self, value, *, returned=None):
        changed = False
        for row in value["requests"]:
            if row["state"] == "released":
                continue
            try:
                proof = self.proof(row, returned=row["execution"] == returned)
                reason = (
                    "Awaiting attributed cleanup or fenced non-dispatch evidence: "
                    + str(ProcessBarrier(self.directory, row["track"]).path)
                    if row["state"] == "admitted"
                    else row["wait_reason"]
                )
            except ProcessBarrierError as error:
                proof, reason = None, str(error)
            if proof is not None:
                row.update(
                    state="released",
                    released_at=time.time(),
                    release_evidence=proof,
                    reason="Verification slot released with attributed evidence",
                )
                changed = True
            elif row.get("reason") != reason:
                row["reason"] = reason
                changed = True
        return changed

    @contextmanager
    def slot(self, task, execution, *, stage, workspace, head, scope, check):
        if (
            any(execution[key] != task[key] for key in ("track", "attempt"))
            or Path(execution["directory"]) != self.directory
        ):
            raise ProcessBarrierError("Verification request has a different launch identity")
        check()
        reason = "Waiting for project verification capacity in registration order"
        row = {
            **{key: execution[key] for key in ("track", "attempt", "execution")},
            "task": task["id"],
            "generation": task["generation"],
            "stage": stage,
            "workspace": str(workspace),
            "head": head,
            "scope": scope,
            "state": "waiting",
            "queued_at": time.time(),
            "wait_reason": reason,
            "reason": reason,
        }
        key = execution["execution"]
        with self.locked():
            value = self.read()
            if any(item["execution"] == key for item in value["requests"]):
                raise ProcessBarrierError("Verification execution was already queued")
            value["requests"].append(row)
            self.write(value)
        try:
            while True:
                check()
                with self.locked():
                    value = self.read()
                    changed = self.refresh(value)
                    row = next(item for item in value["requests"] if item["execution"] == key)
                    if row["state"] != "waiting":
                        raise ProcessBarrierError(
                            "Verification request was fenced before admission"
                        )
                    waiting = [item for item in value["requests"] if item["state"] == "waiting"]
                    active = sum(item["state"] == "admitted" for item in value["requests"])
                    admitted = waiting[0]["execution"] == key and active < self.limit
                    if admitted:
                        row.update(
                            state="admitted",
                            admitted_at=time.time(),
                            reason="Verification capacity reserved before dispatch",
                        )
                    if changed or admitted:
                        self.write(value)
                if admitted:
                    break
                time.sleep(0.1)
            check()
            yield
        finally:
            # A finally block is not exit proof. Consumed/unconfirmed launches
            # remain admitted even if the caller raised or lost its claim.
            with self.locked():
                value = self.read()
                if self.refresh(value, returned=key):
                    self.write(value)


@contextmanager
def slot(directory, limit, task, execution, **kwargs):
    if limit is None:
        yield
        return
    with VerificationCapacity(directory, limit).slot(task, execution, **kwargs):
        yield
