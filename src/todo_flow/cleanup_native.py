"""Consume native viewer retirement evidence without replaying terminal effects."""

import json
from pathlib import Path
import subprocess

from . import managed_workspace as managed
from .store import fingerprint


IDENTITY_KEYS = (
    "handle",
    "tabId",
    "ptyId",
    "incarnationId",
    "worktreeId",
    "executionHostId",
)


def read_record(folder, name):
    value = json.loads((folder / name).read_text())
    if not isinstance(value, dict):
        raise ValueError("Native cleanup evidence must be an object: " + name)
    return value


def native_terminal_cleanup(folder, launch, previous=None):
    """Require attributed retirement and fresh absence, including on cleanup retry."""
    item = {
        "attempt": folder.name,
        "backend": "orca",
        "execution_mode": "orca-native",
        "status": "preserved",
    }
    try:
        spec = read_record(folder, "native-spec.json")
        record = read_record(folder, "native-session.json")
        intent = read_record(folder, "native-viewer-close-intent.json")
        closed = read_record(folder, "native-viewer-close.json")
        retired = read_record(folder, "native-viewer-retired.json")
        terminal = launch.get("terminal", {})
        runtime = record.get("runtime_id")
        if (
            launch.get("backend") != "orca"
            or spec.get("folder") != str(folder)
            or spec.get("task", {}).get("attempt") != folder.name
            or record.get("task") != spec.get("task")
            or type(record.get("version")) is not int
            or record["version"] != 1
            or record.get("host") != "local"
            or not spec.get("head")
            or record.get("head") != spec["head"]
            or not spec.get("worktree")
            or record.get("worktree") != spec["worktree"]
            or launch.get("worktree") != spec["worktree"]
            or not spec.get("cli")
            or launch.get("cli") != spec["cli"]
            or not isinstance(runtime, str)
            or not runtime
            or any(not launch.get(key) for key in ("session", "turn"))
            or any(launch[key] != record.get(key) for key in ("session", "turn"))
            or any(
                not isinstance(terminal.get(key), str) or not terminal[key]
                for key in IDENTITY_KEYS
            )
            or terminal["worktreeId"] != spec["worktree"]
            or terminal["executionHostId"] != "local"
            or record.get("terminal") != terminal
            or intent.get("terminal") != terminal
            or intent.get("runtime_id") != runtime
        ):
            raise ValueError("Native attempt or viewer ownership evidence does not match")
        if (
            record.get("status") not in ("server-stopped", "complete")
            or type(record.get("server_exit")) is not int
            or record.get("viewer_cleanup_error") is not None
        ):
            raise ValueError("Native server exit or viewer retirement is unconfirmed")
        receipt = closed.get("result", {}).get("close", {})
        if (
            closed.get("ok") is not True
            or closed.get("_meta", {}).get("runtimeId") != runtime
            or receipt.get("handle") != terminal["handle"]
            or receipt.get("tabId") != terminal["tabId"]
            or type(receipt.get("ptyKilled")) is not bool
            or retired.get("handle") != terminal["handle"]
            or retired.get("runtime_id") != runtime
            or retired.get("close_receipt") != str(folder / "native-viewer-close.json")
            or retired.get("complete_inventory_absent") is not True
        ):
            raise ValueError("Native viewer retirement receipt is missing or mismatched")
        # retire_viewer also permits ptyKilled=false after a confirmed PTY exit.
        # Its final retirement receipt certifies those checks and complete absence.
        binding = fingerprint(
            [
                spec["task"],
                spec["head"],
                spec["worktree"],
                terminal,
                runtime,
                record["session"],
                record["turn"],
            ]
        )
        if previous and previous.get("execution_mode") == "orca-native":
            if previous.get("binding", binding) != binding:
                raise ValueError("Native cleanup target changed since the previous observation")
        item.update(handle=terminal["handle"], binding=binding)
        # Use the repository cwd: the attempt's checkout may already be gone.
        response = managed._call(
            launch["cli"],
            ["terminal", "list", "--limit", "100000", "--json"],
            launch["repo"],
        )
        inventory = response["result"]
        rows = inventory.get("terminals")
        scope = inventory.get("hostScope", {})
        if (
            response.get("_meta", {}).get("runtimeId") != runtime
            or not isinstance(rows, list)
            or inventory.get("truncated") is not False
            or type(inventory.get("totalCount")) is not int
            or inventory["totalCount"] != len(rows)
            or "local" not in scope.get("hostIds", [])
            or "local" in scope.get("omittedHostIds", [])
            or any(
                not isinstance(row, dict)
                or any(
                    not isinstance(row.get(key), str) or not row[key]
                    for key in ("handle", "tabId", "ptyId", "executionHostId")
                )
                for row in rows
            )
        ):
            raise ValueError("Native viewer inventory is incomplete or its runtime changed")
        if any(
            any(row[key] == terminal[key] for key in ("handle", "tabId", "ptyId"))
            for row in rows
        ):
            raise ValueError("Native viewer resource is present; preserve possible user reuse")
        return {**item, "status": "absent"}
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        return {**item, "reason": str(error)}
