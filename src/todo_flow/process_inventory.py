"""Fenced, durable inventory of every worker/verification launch in an attempt."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid

from .process_barrier import ProcessBarrierError


def check_worker_results(value):
    results = value.get("worker_results")
    if results is None:
        return
    if (
        not isinstance(results, dict)
        or type(results.get("version")) is not int
        or results["version"] != 1
        or not isinstance(results.get("executions"), dict)
    ):
        raise ValueError("Unsupported worker observation format")
    for execution, observation in results["executions"].items():
        if (
            execution not in value["executions"]
            or not isinstance(observation, dict)
            or observation.get("phase") not in ("execution", "decoding", "provider", "validation")
            or observation.get("status") not in ("valid", "error")
            or any(
                observation.get(key) is not None and type(observation[key]) is not bool
                for key in ("parsed", "validated")
            )
        ):
            raise ValueError("Invalid or misattributed worker observation")
        if observation["status"] == "valid" and (
            not value["executions"][execution]
            or observation.get("parsed") is not True
            or observation.get("validated") is not True
        ):
            raise ValueError("Valid proposal requires a prepared execution and validation")


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
            check_worker_results(value)
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

    def observe_worker(self, execution, observation):
        """Attach proposal evidence; this never confirms process or track success."""
        with self.locked():
            value = self.read()
            if value["state"] != "open" or execution not in value["executions"]:
                raise ProcessBarrierError("Worker observation belongs to a fenced execution")
            results = value.setdefault("worker_results", {"version": 1, "executions": {}})
            previous = results["executions"].get(execution)
            if previous is not None:
                if previous == observation:
                    return
                raise ProcessBarrierError("Cannot replace an execution's worker observation")
            results["executions"][execution] = observation
            try:
                check_worker_results(value)
            except ValueError as error:
                raise ProcessBarrierError(str(error)) from error
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
