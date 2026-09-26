"""Fenced, durable inventory of every worker/verification launch in an attempt."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid

from .process_barrier import ProcessBarrierError


class ProcessInventory:
    def __init__(self, directory, track, attempt, task=None, generation=None):
        self.directory = Path(directory)
        self.identity = {"track": track, "attempt": attempt}
        self.claim = {"task": task, "generation": generation}
        key = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()
        self.path = self.directory / (key + ".inventory.json")
        self.lock = self.directory / (key + ".inventory.lock")

    @contextmanager
    def locked(self):
        with self.lock.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            if (
                type(value.get("version")) is not int
                or value.get("version") != 1
                or value.get("identity") != self.identity
                or value.get("state") not in ("open", "sealed")
                or not isinstance(value.get("executions"), dict)
            ):
                raise ValueError("Unsupported or misattributed inventory")
            for key, prepared in value["executions"].items():
                if not isinstance(key, str) or not key or type(prepared) is not bool:
                    raise ValueError("Invalid execution inventory")
            if self.claim["task"] is not None and value.get("claim") != self.claim:
                raise ValueError("Inventory belongs to another claim")
            return value
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ProcessBarrierError(
                f"Cannot inspect process inventory {self.path}: {error}"
            ) from error

    def write(self, value):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def start(self):
        with self.locked():
            if self.path.exists():
                if self.read()["state"] != "open":
                    raise ProcessBarrierError("Cannot reopen a sealed attempt inventory")
                return
            self.write(
                {
                    "version": 1,
                    "identity": self.identity,
                    "claim": self.claim,
                    "state": "open",
                    "executions": {},
                }
            )

    def seal(self):
        with self.locked():
            value = self.read()
            value["state"] = "sealed"
            self.write(value)
            return value

    @contextmanager
    def lifecycle(self):
        self.start()
        try:
            yield self
        finally:
            self.seal()

    def register(self):
        with self.locked():
            value = self.read()
            if value["state"] != "open":
                raise ProcessBarrierError("Attempt inventory is fenced; no more launches")
            execution = uuid.uuid4().hex
            value["executions"][execution] = False
            self.write(value)
        return {"directory": str(self.directory), **self.identity, "execution": execution}

    def prepared(self, execution):
        with self.locked():
            value = self.read()
            if value["state"] != "open" or execution not in value["executions"]:
                raise ProcessBarrierError("Launch no longer belongs to an open attempt")
            value["executions"][execution] = True
            self.write(value)


def launch_identity(directory, task):
    inventory = ProcessInventory(
        directory,
        task.get("track", "worker"),
        task["attempt"],
        task.get("id"),
        task.get("generation"),
    )
    inventory.start()
    return inventory.register()


def note_prepared(identity):
    inventory = ProcessInventory(identity["directory"], identity["track"], identity["attempt"])
    # Standalone supervisor callers may provide their own launch intent. Such
    # receipts never authorize Engine recovery without an attempt inventory.
    if inventory.path.exists():
        inventory.prepared(identity["execution"])
