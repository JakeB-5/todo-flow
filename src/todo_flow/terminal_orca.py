"""Retire owned Orca exec-bridge tabs using the existing public CLI.

Decision decision-95af607c8a6244a8 permits input racing after inspection.
It does not permit skipping ownership, activity or exit checks. No external
atomic-close API is required. Every response and close intent is retained;
ptyKilled alone never releases capacity. Only a complete, same-runtime local
inventory proving physical absence does that.
"""

import json
import math
from pathlib import Path
import subprocess

from .maintenance import write_json
from .terminal_retirement import TerminalObservation
from .terminal_slots import TerminalCapacityError


IDENTITY_KEYS = (
    "handle",
    "ptyId",
    "incarnationId",
    "executionHostId",
    "worktreeId",
    "tabId",
    "title",
)
DECISION = "decision-95af607c8a6244a8"


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


class OrcaTerminalAdapter:
    def __init__(self, launch):
        self.launch = launch
        self.recorded = launch.get("terminal") or {}
        self.creation = self.recorded.get("_todo_flow") or {}
        self.journal = None

    def record(self, event):
        value = json.loads(self.journal.read_text()) if self.journal.exists() else []
        value.append({"decision": DECISION, **event})
        write_json(self.journal, value)
        return len(value)

    def request(self, args):
        try:
            completed = subprocess.run(
                [self.launch["cli"], "terminal", *args, "--json"],
                cwd=self.launch["repo"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.record({"args": args, "error": str(error)})
            raise
        self.record(
            {
                "args": args,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        payload = json.loads(completed.stdout)
        if payload.get("_meta", {}).get("runtimeId") != self.creation["runtimeId"]:
            raise TerminalCapacityError("Orca runtime changed; preserve the original resource")
        if completed.returncode or payload.get("ok") is not True:
            raise TerminalCapacityError("Orca observation failed; inspect retirement journal")
        return payload["result"]

    def inspect(self, resource):
        folder = Path(resource["launch_record"]).parent
        self.journal = folder / "orca-retirement.json"

        def observed(status, reason, terminal=None):
            sequence = self.record({"status": status, "reason": reason, "terminal": terminal})
            proof = json.dumps(
                {
                    "journal": str(self.journal),
                    "sequence": sequence,
                    "runtimeId": self.creation.get("runtimeId"),
                    "terminal": terminal,
                    "reason": reason,
                },
                sort_keys=True,
            )
            token = json.dumps(terminal, sort_keys=True) if status == "idle" else ""
            return TerminalObservation(status, resource, proof, token)

        if (
            resource.get("backend") != "orca"
            or resource.get("terminal") != self.recorded
            or not all(isinstance(self.recorded.get(k), str) and self.recorded[k] for k in IDENTITY_KEYS)
            or self.recorded["executionHostId"] != "local"
            or self.launch.get("worktree") != "id:" + self.recorded["worktreeId"]
            or not self.creation.get("runtimeId")
            or self.creation.get("exec_bridge") is not True
            or not number(self.creation.get("started_at"))
        ):
            return observed("unknown", "Dispatch identity or exec-bridge evidence is incomplete")
        receipt = json.loads((folder / "terminal-process.json").read_text())
        finished = receipt.get("finished_at")
        if (
            receipt.get("status") != "exited"
            or receipt.get("cleanup_confirmed") is not True
            or type(receipt.get("returncode")) is not int
            or not number(finished)
        ):
            return observed("unknown", "Execution exit is unconfirmed")
        inventory = self.request(
            ["list", "--worktree", self.launch["worktree"], "--limit", "100000"]
        )
        rows = inventory.get("terminals")
        scope = inventory.get("hostScope", {})
        if (
            not isinstance(rows, list)
            or inventory.get("truncated") is not False
            or type(inventory.get("totalCount")) is not int
            or inventory["totalCount"] != len(rows)
            or scope.get("hostIds") != ["local"]
            or scope.get("omittedHostIds") != []
            or any(
                not isinstance(row, dict)
                or row.get("executionHostId") != "local"
                or row.get("worktreeId") != self.recorded["worktreeId"]
                or not all(row.get(k) for k in ("handle", "tabId", "ptyId"))
                for row in rows
            )
        ):
            return observed("unknown", "Inventory is incomplete or has an unexpected host scope")
        matches = [
            row
            for row in rows
            if any(row.get(k) == self.recorded[k] for k in ("handle", "ptyId", "tabId"))
        ]
        if not matches:
            return observed("absent", "Complete same-runtime inventory excludes the tab and PTY")
        if len(matches) != 1:
            return observed("busy", "The tab has additional or conflicting physical owners")
        current = matches[0]
        if any(current.get(k) != self.recorded[k] for k in IDENTITY_KEYS):
            return observed("busy", "Terminal identity or title changed", current)
        # The process receipt precedes the bridge's final print and PTY exit.
        # A short wait allows immediate retirement without killing that bridge.
        # A negative/failed wait leaves capacity charged and is reobserved later.
        waited = self.request(
            ["wait", "--terminal", current["handle"], "--for", "exit", "--timeout-ms", "1000"]
        ).get("wait", {})
        if (
            waited.get("handle") != current["handle"]
            or waited.get("condition") != "exit"
            or waited.get("satisfied") is not True
            or waited.get("status") != "exited"
            or type(waited.get("exitCode")) is not int
            or waited["exitCode"] != receipt["returncode"] % 256
        ):
            return observed("pending", "PTY exit has not been confirmed", current)
        shown = self.request(["show", "--terminal", current["handle"]])["terminal"]
        if any(shown.get(k) != self.recorded[k] for k in IDENTITY_KEYS):
            return observed("busy", "Terminal identity changed during exit observation", shown)
        current = {**current, **shown}
        marker = f"TODO Flow worker exited: {receipt['returncode']}"
        preview = current.get("preview", "")
        last_input = current.get("lastInputAt")
        if (
            current.get("connected") is not False
            or current.get("writable") is not False
            or current.get("orphaned") is not False
            or current.get("agentIdentity")
            or not number(current.get("lastOutputAt"))
            or current["lastOutputAt"] > (finished + 2) * 1000
            or (
                last_input is not None
                and (
                    not number(last_input)
                    or last_input > self.creation["started_at"] * 1000
                )
            )
            or not isinstance(preview, str)
            or marker not in preview
            or preview.rsplit(marker, 1)[-1].strip()
        ):
            return observed("busy", "Activity or terminal state does not identify an exited bridge", current)
        return observed("idle", "Owned exec bridge and PTY exited; no subsequent activity", current)

    def close(self, observation):
        proof = json.loads(observation.proof)
        current = proof["terminal"]
        if (
            observation.status != "idle"
            or proof["runtimeId"] != self.creation["runtimeId"]
            or any(current.get(k) != self.recorded[k] for k in IDENTITY_KEYS)
        ):
            raise TerminalCapacityError("Close lacks an attributed exit observation")
        previous = json.loads(self.journal.read_text())
        if any(event.get("close_intent") for event in previous):
            raise TerminalCapacityError("Close was already dispatched or interrupted; reobserve only")
        self.record({"close_intent": True, "observation": proof})
        # The ledger's closing state and this intent precede dispatch. Recovery
        # never repeats it, including when the CLI response or journal write fails.
        # Decision DECISION explicitly permits input racing after inspection.
        self.request(["close", "--terminal", current["handle"]])
        # ptyKilled=false is expected for an already-exited exec bridge. Even a
        # true value is not physical tab absence; the caller must inspect again.
