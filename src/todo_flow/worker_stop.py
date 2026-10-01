"""Proposal observations and a read-only view of existing execution evidence.

Neither a zero return code nor a valid proposal establishes track completion.
This view is never consumed as cleanup, claim, adoption, or landing authority.
"""

import json
from pathlib import Path
import re

from .process_barrier import ProcessBarrier, ProcessBarrierError
from .process_inventory import ProcessInventory, launch_identity


def observe_launch(state, task, observation):
    identity = launch_identity(state, task)
    observation["identity"] = identity
    return identity


def validate_proposal(result, kind, observation):
    from .worker import validate

    observation.update(phase="validation", parsed=True)
    result = validate(result, kind)
    observation.update(validated=True, status="valid")
    return result


def save_observation(state, task, observation):
    identity = observation.get("identity")
    if identity is None:
        return
    inventory = ProcessInventory(
        state,
        task.get("track", "worker"),
        task["attempt"],
        task.get("id"),
        task.get("generation"),
    )
    inventory.observe_worker(
        identity["execution"],
        {key: value for key, value in observation.items() if key != "identity"},
    )


def _receipt(folder, name, identity, task):
    path = folder / name
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError("Invalid execution receipt")
    if "version" not in record:
        return {"path": str(path), "support": "legacy"}
    if type(record["version"]) is not int or record["version"] != 1:
        raise ValueError("Unsupported execution receipt version")
    if name == "native-session.json":
        previous = record.get("task", {})
        if not isinstance(previous, dict):
            raise ValueError("Invalid native task identity")
        if "execution" not in record:
            return {"path": str(path), "support": "legacy"}
        matches = record["execution"] == identity["execution"] and all(
            previous.get(key) == task.get(key) for key in ("id", "generation", "attempt", "track")
        )
        fields = ("status", "server_pid", "server_exit", "failure", "server_group_exit_confirmed")
    else:
        matches = record.get("identity") == identity
        fields = ("status", "pid", "returncode", "finished_at", "error", "cleanup_error")
    if not matches:
        raise ValueError("Execution receipt belongs to another execution or claim")
    return {
        "path": str(path),
        "support": "current",
        "record": {key: record[key] for key in fields if key in record},
    }


def worker_stop(state, task):
    """Resolve only the requested attempt, preserving unknown and legacy evidence."""
    attempt = task.get("attempt")
    result = {"version": 1, "attempt": attempt, "support": "missing", "executions": []}
    if attempt is None:
        return result
    if not isinstance(attempt, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", attempt):
        raise ValueError("Invalid attempt ID")
    inventory = ProcessInventory(
        state, task.get("track", "worker"), attempt, task.get("id"), task.get("generation")
    )
    folder = Path(state) / "attempts" / attempt
    barrier = ProcessBarrier(state, task.get("track", "worker"))
    result["sources"] = {
        "inventory": str(inventory.path),
        "journal": str(barrier.path),
        **{
            name: str(folder / name)
            for name in ("output.json", "final.json", "native-proposal.json", "stderr.log")
        },
    }
    if not inventory.path.exists() and not inventory.path.is_symlink():
        result["support"] = "legacy"
        return result
    try:
        value = inventory.read()
        events = [event for event in barrier.history() if event["attempt"] == attempt]
        observations = value.get("worker_results", {}).get("executions", {})
        entries = []
        for execution, prepared in value["executions"].items():
            identity = {**inventory.identity, "execution": execution}
            history = [event for event in events if event["execution"] == execution]
            start = next((event for event in history if event["state"] == "running"), None)
            latest = history[-1] if history else None
            exit_event = latest if latest and latest["state"] == "confirmed" else None
            evidence = exit_event["evidence"] if exit_event else {}
            observation = observations.get(execution)
            entry = {
                "identity": identity,
                "prepared": prepared,
                "support": "current" if observation is not None else "legacy",
                "start": start,
                "latest": latest,
                "exit": exit_event,
                "returncode": evidence.get("returncode"),
                "completion": evidence.get("completion"),
                "proposal": observation,
                "usage": observation.get("usage") if observation else None,
                "cost": observation.get("cost") if observation else None,
                "provider_reason": observation.get("provider_reason") if observation else None,
                "receipts": [],
            }
            # Attempt-local receipts describe the worker, not later verification
            # executions that may share its inventory. Only attach to that worker.
            if observation is not None or len(value["executions"]) == 1:
                for name in ("terminal-process.json", "native-session.json"):
                    receipt = _receipt(folder, name, identity, task)
                    if receipt is not None:
                        entry["receipts"].append(receipt)
            entries.append(entry)
        result.update(
            support="current" if "worker_results" in value else "legacy",
            executions=entries,
        )
    except (OSError, ValueError, TypeError, AttributeError, ProcessBarrierError) as error:
        # Never display a partially accepted set as current evidence.
        result.update(support="unreadable", executions=[], error=str(error))
    return result
