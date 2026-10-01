"""Join existing process receipts with fenced proposal observations.

`todo-flow --state STATE worker-stop TRACK ATTEMPT` is a read-only query.
Neither a valid proposal nor a confirmed process exit establishes goal success.
"""

import json
from pathlib import Path

from .process_barrier import ProcessBarrier
from .process_inventory import ProcessInventory, launch_identity


class WorkerObservation:
    """Record one invocation, after its adapter has finished cleanup."""

    def __init__(self, task):
        self.task = task
        self.identity = None
        self.inventory = None
        self.phase = "execution"
        self.parsed = None
        self.validated = None

    def __enter__(self):
        return self

    def launch(self, state):
        if self.identity is not None:
            raise RuntimeError("A worker observation cannot span multiple executions")
        self.identity = launch_identity(state, self.task)
        self.inventory = ProcessInventory(
            state,
            self.identity["track"],
            self.identity["attempt"],
            self.task.get("id"),
            self.task.get("generation"),
        )
        return self.identity

    def validate(self, result, kind):
        from .worker import validate

        self.phase = "validation"
        self.parsed = True
        self.validated = False
        result = validate(result, kind)
        self.validated = True
        return result

    def __exit__(self, error_type, error, traceback):
        if self.identity is None:
            return False
        if error is None and self.validated is not True:
            raise RuntimeError("Worker returned without proposal validation")
        reason = None
        if error is not None:
            if isinstance(error, TimeoutError):
                reason = "timeout"
            elif self.phase in ("decoding", "validation"):
                reason = "protocol-error"
            elif self.phase == "provider":
                reason = "provider-error"
            if self.phase == "decoding":
                self.parsed = False
        self.inventory.observe_worker(
            self.identity["execution"],
            {
                "status": "valid" if error is None else "error",
                "phase": self.phase,
                "parsed": self.parsed,
                "validated": self.validated,
                "reason": reason,
                "error": (
                    {"type": error_type.__name__, "message": str(error)}
                    if error is not None
                    else None
                ),
            },
        )
        return False


def _record(path):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError(f"Dangling receipt: {path}") from None
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Invalid receipt object: {path}")
    return value


def _receipts(directory, track, attempt):
    folder = directory / "attempts" / attempt
    linked = {}
    unattributed = []
    for kind, receipt_name, spec_name, identity_key in (
        ("terminal", "terminal-process.json", "terminal-spec.json", "launch_identity"),
        ("native", "native-session.json", "native-spec.json", "process_identity"),
    ):
        path = folder / receipt_name
        record = _record(path)
        if record is None:
            continue
        if kind == "native":
            if type(record.get("version")) is not int or record["version"] != 1:
                raise ValueError(f"Unsupported native receipt: {path}")
        elif "version" in record:
            raise ValueError(f"Unsupported terminal receipt: {path}")
        source = {"path": str(path), "record": record}
        spec = _record(folder / spec_name)
        identity = (spec or {}).get(identity_key)
        if (
            not isinstance(identity, dict)
            or identity.get("track") != track
            or identity.get("attempt") != attempt
            or not isinstance(identity.get("execution"), str)
            or not isinstance(identity.get("directory"), str)
            or Path(identity["directory"]).resolve() != directory
        ):
            unattributed.append(source)
            continue
        if kind == "native" and (
            spec.get("execution") != identity["execution"]
            or record.get("task") != spec.get("task")
            or not isinstance(record.get("task"), dict)
            or record["task"].get("track") != track
            or record["task"].get("attempt") != attempt
        ):
            raise ValueError(f"Misattributed native receipt: {path}")
        linked.setdefault(identity["execution"], {})[kind] = source
    return linked, unattributed


def worker_stop_summary(directory, track, attempt):
    """Project original evidence without writing, recovery, or success inference.

    Legacy means no versioned proposal observations, not a successful worker.
    Unsupported or corrupt formats raise instead of falling back to legacy.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError("Worker stop query requires an existing state directory")
    if not isinstance(attempt, str) or not attempt or Path(attempt).name != attempt:
        raise ValueError("Worker stop query requires an attempt ID")
    if attempt in (".", ".."):
        raise ValueError("Worker stop query requires an attempt ID")
    inventory = ProcessInventory(directory, track, attempt)
    value = (
        inventory.read()
        if inventory.path.exists() or inventory.path.is_symlink()
        else {"executions": {}}
    )
    observations = value.get("worker_results")
    barrier = ProcessBarrier(directory, track)
    events = [event for event in barrier.history() if event["attempt"] == attempt]
    receipts, unattributed = _receipts(directory, track, attempt)
    executions = dict(value["executions"])
    for event in events:
        executions.setdefault(event["execution"], None)
    rows = []
    for execution, prepared in executions.items():
        history = [event for event in events if event["execution"] == execution]
        latest = history[-1] if history else None
        evidence = latest["evidence"] if latest else {}
        outcome = evidence.get("outcome") if latest and latest["state"] == "confirmed" else None
        started = (
            False
            if outcome == "not-spawned"
            else True
            if outcome == "group-exited" or any(e["state"] == "running" for e in history)
            else None
        )
        code = evidence.get("returncode")
        rows.append(
            {
                "execution": execution,
                "prepared": prepared,
                "process": {
                    "state": latest["state"] if latest else "unknown",
                    "started": started,
                    "group_exit_confirmed": (
                        True
                        if outcome == "group-exited"
                        else False
                        if outcome == "not-spawned"
                        else None
                    ),
                    "returncode": code if type(code) is int else None,
                    "reason": evidence.get("completion"),
                    "events": history,
                },
                "proposal": observations["executions"].get(execution) if observations else None,
                "receipts": receipts.pop(execution, {}),
                "usage": None,
                "cost": None,
                "goal_success": None,
            }
        )
    for sources in receipts.values():
        unattributed.extend(sources.values())
    folder = directory / "attempts" / attempt
    return {
        "version": 1,
        "support": "current" if observations is not None else "legacy",
        "identity": {"track": track, "attempt": attempt},
        "inventory": str(inventory.path),
        "journal": str(barrier.path),
        "executions": rows,
        "unattributed_receipts": unattributed,
        "artifacts": {
            name: str(folder / name)
            for name in (
                "output.json",
                "final.json",
                "stderr.log",
                "native-proposal.json",
                "native-server.log",
            )
            if (folder / name).is_file()
        },
    }
