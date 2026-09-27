"""Observe automatic tmux window removal without issuing destructive commands.

A window ID is unique only within one server lifetime. Capture the local socket
incarnation before dispatch and require it before and after a complete inventory
query. Missing sockets, replaced sockets, failed queries and retained windows
are uncertainty, not proof of release. In particular, remain-on-exit windows are
never killed: tmux exposes no activity-conditional close contract here.
"""

import json
from pathlib import Path
import re
import stat
import subprocess

from .terminal_retirement import TerminalObservation
from .terminal_slots import TerminalCapacityError


def socket_identity(socket):
    if not isinstance(socket, str) or not Path(socket).is_absolute():
        return None
    try:
        value = Path(socket).lstat()
    except OSError:
        return None
    if not stat.S_ISSOCK(value.st_mode):
        return None
    return {
        "path": socket,
        "device": value.st_dev,
        "inode": value.st_ino,
        "ctime_ns": value.st_ctime_ns,
    }


class TmuxTerminalAdapter:
    def __init__(self, launch):
        self.socket = launch.get("socket")
        self.identity = launch.get("tmux_socket_identity")

    def inspect(self, resource):
        def unknown(reason):
            return TerminalObservation("unknown", resource, reason)

        handle = resource.get("handle")
        if resource.get("backend") != "tmux" or not isinstance(handle, str):
            return unknown("Missing tmux resource identity")
        if re.fullmatch(r"@[0-9]+", handle) is None:
            return unknown("Invalid tmux window ID")
        if not self.identity or resource.get("tmux_socket_identity") != self.identity:
            return unknown("Missing dispatch-time tmux socket incarnation")
        if socket_identity(self.socket) != self.identity:
            return unknown("Tmux socket disappeared or changed; reconcile the original server")
        try:
            result = subprocess.run(
                ["tmux", "-S", self.socket, "list-windows", "-a", "-F", "#{window_id}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return unknown(f"Tmux inventory is unavailable: {error}")
        if socket_identity(self.socket) != self.identity:
            return unknown("Tmux socket changed during inventory observation")
        windows = result.stdout.splitlines()
        if not windows or any(re.fullmatch(r"@[0-9]+", item) is None for item in windows):
            return unknown("Tmux inventory is empty or malformed")
        if handle in windows:
            return unknown(
                "Tmux window remains present; no activity-conditional close is available"
            )
        proof = json.dumps(
            {"socket": self.identity, "windows": sorted(set(windows)), "absent": handle},
            sort_keys=True,
        )
        return TerminalObservation("absent", resource, proof)

    def close(self, observation):
        raise TerminalCapacityError("Tmux adapter only confirms automatic window removal")
