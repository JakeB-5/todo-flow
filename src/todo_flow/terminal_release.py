"""Host-only retirement entry point shared by worker exit and later cleanup.

Adapters verify ownership, activity and exit before close. The Orca policy in
decision-95af607c8a6244a8 permits input racing after that inspection; it does not
require an external atomic-close API. No adapter is selected by worker output
or executable names in a launch record.
"""

import json
from pathlib import Path
import subprocess
from typing import Protocol

from .maintenance import write_json
from .terminal_orca import OrcaTerminalAdapter
from .terminal_retirement import TerminalObservation, retire_terminal
from .terminal_slots import TerminalCapacityError, TerminalSlots
from .terminal_tmux import TmuxTerminalAdapter


class TerminalAdapter(Protocol):
    def inspect(self, resource: dict) -> TerminalObservation:
        """Return an attributed inventory observation, including activity."""
        ...

    def close(self, observation: TerminalObservation) -> None:
        """Close only the verified owned terminal under the backend policy."""
        ...


class UnsupportedTerminalAdapter:
    def inspect(self, resource):
        return TerminalObservation(
            "unknown", resource, "Backend has no verified retirement adapter"
        )

    def close(self, observation):
        raise TerminalCapacityError("Backend cannot safely close this terminal")


def terminal_adapter(launch) -> TerminalAdapter:
    if launch.get("backend") == "tmux":
        return TmuxTerminalAdapter(launch)
    if launch.get("backend") == "orca":
        return OrcaTerminalAdapter(launch)
    return UnsupportedTerminalAdapter()


def retire_launch(folder, *, dry_run=False):
    """Reconcile an accepted launch; preserve failures without guessing capacity.

    Process confirmation and current claim are rechecked inside retire_terminal.
    A report failure must never be mistaken for a released lease; the ledger is
    authoritative. Dry runs neither advance the ledger nor call the adapter.
    """
    folder = Path(folder)
    report = {"attempt": folder.name, "status": "preserved"}
    try:
        launch = json.loads((folder / "launch.json").read_text())
        report["backend"] = launch.get("backend")
        lease = launch["terminal_slot"]
        identity = json.loads((folder / "terminal-spec.json").read_text())["launch_identity"]
        directory = Path(identity["directory"]).resolve()
        ledger = Path(launch["terminal_ledger"]).resolve()
        if ledger != directory / "terminal-slots.json":
            raise TerminalCapacityError("Terminal ledger does not match execution directory")
        if any(lease["owner"][key] != identity[key] for key in ("track", "attempt", "execution")):
            raise TerminalCapacityError("Terminal lease does not match launch identity")
        resource = {
            "backend": launch["backend"],
            "terminal": launch.get("terminal"),
            "handle": launch.get("handle"),
            "launch_record": str(folder / "launch.json"),
        }
        if launch["backend"] == "tmux" and "tmux_socket_identity" in lease["resource"]:
            resource["tmux_socket_identity"] = launch.get("tmux_socket_identity")
        if lease["resource"] != resource or lease["owner"]["attempt"] != folder.name:
            raise TerminalCapacityError("Terminal resource does not match accepted launch")
        limits = json.loads(ledger.read_text())["limits"]
        slots = TerminalSlots(
            directory, concurrency=limits["concurrency"], idle_limit=limits["idle"]
        )
        receipt = json.loads((folder / "terminal-process.json").read_text())
        if (
            receipt.get("status") != "exited"
            or receipt.get("cleanup_confirmed") is not True
            or type(receipt.get("returncode")) is not int
        ):
            raise TerminalCapacityError("Worker group exit is unconfirmed")
        if dry_run:
            report["reason"] = "Ledger retirement requires a confirmed physical observation"
            return report
        adapter = terminal_adapter(launch)
        current = retire_terminal(
            slots, lease, inspect_resource=adapter.inspect, close_resource=adapter.close
        )
        report["slot"] = current["slot"]
        report["state"] = current["state"]
        report["evidence"] = current["evidence"]
        if current["state"] == "closed":
            report["status"] = "closed"
        else:
            report["reason"] = "Physical retirement is unconfirmed; capacity remains charged"
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        report["reason"] = str(error)
    if not dry_run:
        write_json(folder / "terminal-retirement.json", report)
    return report
