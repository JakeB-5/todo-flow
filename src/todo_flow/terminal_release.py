"""Best-effort cleanup of this worker's terminal, independent of new launches."""

import json
from pathlib import Path
import subprocess

from .maintenance import write_json
from .terminal_orca import OrcaTerminalAdapter
from .terminal_retirement import TerminalObservation, TerminalRetirementError, retire_terminal
from .terminal_tmux import TmuxTerminalAdapter


class UnsupportedTerminalAdapter:
    def inspect(self, resource):
        return TerminalObservation(
            "unknown", resource, "Backend has no verified retirement adapter"
        )

    def close(self, observation):
        raise TerminalRetirementError("Backend cannot safely close this terminal")


def terminal_adapter(launch):
    if launch.get("backend") == "tmux":
        return TmuxTerminalAdapter(launch)
    if launch.get("backend") == "orca":
        return OrcaTerminalAdapter(launch)
    return UnsupportedTerminalAdapter()


def retire_launch(folder, *, dry_run=False):
    folder = Path(folder)
    report = {"attempt": folder.name, "status": "preserved"}
    try:
        launch = json.loads((folder / "launch.json").read_text())
        report["backend"] = launch.get("backend")
        identity = json.loads((folder / "terminal-spec.json").read_text())["launch_identity"]
        # Older launches can still be cleaned using their recorded ownership.
        # Their capacity ledger is historical evidence, never a prerequisite.
        legacy = launch.get("terminal_slot", {})
        owner = launch.get("owner", legacy.get("owner"))
        if (
            not isinstance(owner, dict)
            or owner.get("attempt") != folder.name
            or any(owner.get(key) != identity[key] for key in ("track", "attempt", "execution"))
        ):
            raise TerminalRetirementError("Terminal ownership does not match launch identity")
        resource = {
            "backend": launch["backend"],
            "terminal": launch.get("terminal"),
            "handle": launch.get("handle"),
            "launch_record": str(folder / "launch.json"),
        }
        if launch["backend"] == "tmux":
            resource["tmux_socket_identity"] = launch.get("tmux_socket_identity")
        if legacy and legacy.get("resource") != resource:
            raise TerminalRetirementError("Terminal resource does not match recorded ownership")
        receipt = json.loads((folder / "terminal-process.json").read_text())
        if (
            receipt.get("status") != "exited"
            or receipt.get("cleanup_confirmed") is not True
            or type(receipt.get("returncode")) is not int
        ):
            raise TerminalRetirementError("Worker group exit is unconfirmed")
        if dry_run:
            return {
                **report,
                "reason": "Owned terminal can be checked for cleanup after process exit",
            }
        adapter = terminal_adapter(launch)
        report = retire_terminal(
            folder,
            owner,
            resource,
            Path(identity["directory"]),
            inspect_resource=adapter.inspect,
            close_resource=adapter.close,
            prior_close=legacy.get("state") == "closing",
        )
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
        try:
            write_json(folder / "terminal-retirement.json", report)
        except OSError as error:
            report["recording_error"] = str(error)
    return report
