"""Synthetic tmux inventory tests; no external server or model is invoked."""

import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_capacity import (
    accept_terminal,
    reconcile_tmux_terminals,
    reserve_terminal,
)
from todo_flow.terminal_release import retire_launch, terminal_adapter
from todo_flow.terminal_slots import TerminalCapacityError
from todo_flow.terminal_tmux import TmuxTerminalAdapter, socket_identity


class TmuxTerminalReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.socket_path = str(self.root / "tmux.sock")
        self.server = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.server.close)
        self.server.bind(self.socket_path)
        self.launch = {
            "backend": "tmux",
            "socket": self.socket_path,
            "tmux_socket_identity": socket_identity(self.socket_path),
        }
        self.resource = {
            "backend": "tmux",
            "handle": "@1",
            "tmux_socket_identity": self.launch["tmux_socket_identity"],
        }

    def inspect(self, output):
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ) as query:
            result = terminal_adapter(self.launch).inspect(self.resource)
        return result, query

    def test_absence_uses_complete_inventory_and_never_closes(self):
        result, query = self.inspect("@0\n@2\n@2\n")
        self.assertEqual(result.status, "absent")
        self.assertEqual(json.loads(result.proof)["absent"], "@1")
        self.assertEqual(
            query.call_args.args[0],
            ["tmux", "-S", self.socket_path, "list-windows", "-a", "-F", "#{window_id}"],
        )
        with self.assertRaises(TerminalCapacityError):
            terminal_adapter(self.launch).close(result)

    def test_present_retained_or_user_window_is_never_declared_idle(self):
        result, query = self.inspect("@0\n@1\n")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(query.call_count, 1)

    def test_empty_malformed_and_truncated_rows_are_not_absence(self):
        for output in ("", "@0\nwarning\n", "@0\n@\n"):
            with self.subTest(output=output):
                self.assertEqual(self.inspect(output)[0].status, "unknown")

    def test_legacy_missing_or_misattributed_identity_never_queries(self):
        for identity in (None, {"path": "another-server"}):
            with self.subTest(identity=identity):
                self.launch["tmux_socket_identity"] = identity
                result, query = self.inspect("@0\n")
                self.assertEqual(result.status, "unknown")
                query.assert_not_called()

    def test_socket_replacement_during_query_does_not_release(self):
        original = self.launch["tmux_socket_identity"]
        with patch(
            "todo_flow.terminal_tmux.socket_identity",
            side_effect=[original, {**original, "inode": original["inode"] + 1}],
        ):
            self.assertEqual(self.inspect("@0\n")[0].status, "unknown")

    def test_missing_socket_and_failed_query_are_not_absence(self):
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 10),
        ):
            result = TmuxTerminalAdapter(self.launch).inspect(self.resource)
            self.assertEqual(result.status, "unknown")
        Path(self.socket_path).unlink()
        result, query = self.inspect("@0\n")
        self.assertEqual(result.status, "unknown")
        query.assert_not_called()

    def test_symlink_and_regular_file_are_not_server_identity(self):
        path = self.root / "alias"
        path.symlink_to(self.socket_path)
        self.assertIsNone(socket_identity(str(path)))
        path.unlink()
        path.write_text("not a socket")
        self.assertIsNone(socket_identity(str(path)))

    def prepare(self, number):
        attempt = f"attempt-{number}"
        folder = self.root / attempt
        folder.mkdir()
        inventory = ProcessInventory(self.root, f"track-{number}", attempt, "task", 1)
        inventory.start()
        identity = inventory.register()
        launcher = {"backend": "tmux", "socket": self.socket_path}
        reservation = reserve_terminal(launcher, identity, folder)
        gate = LaunchGate.prepare(**identity, backend="tmux")
        inventory.prepared(identity["execution"])
        with gate.launching():
            pass
        event = gate._advance(gate._event(), "cleaning", "Synthetic exit", {"fixture": True})
        gate._advance(
            event,
            "confirmed",
            "Synthetic group exit",
            {
                "identity": {key: identity[key] for key in ("track", "attempt", "execution")},
                "outcome": "group-exited",
                "proof": "Synthetic supervisor confirmed group exit",
            },
        )
        record = {
            **launcher,
            "handle": f"@{number + 1}",
            "status": "accepted",
            "terminal_ledger": str(reservation[0].path),
        }
        record["terminal_slot"] = accept_terminal(reservation, record, folder)
        (folder / "launch.json").write_text(json.dumps(record))
        (folder / "terminal-spec.json").write_text(json.dumps({"launch_identity": identity}))
        (folder / "terminal-process.json").write_text(
            json.dumps(
                {
                    "status": "exited",
                    "returncode": (0, 7, 124, 130)[number % 4],
                    "cleanup_confirmed": True,
                }
            )
        )
        (folder / "worker.stdout").write_text(f"synthetic execution {number}\n")
        return folder, reservation[0]

    def test_fifty_automatic_removals_preserve_logs_and_bound_owned_windows(self):
        windows = {"@0"}  # A user window is neither counted nor disposed.
        maximum = 0
        folders = []

        def query(args, **kwargs):
            self.assertEqual(args[-4:], ["list-windows", "-a", "-F", "#{window_id}"])
            return subprocess.CompletedProcess(args, 0, "\n".join(sorted(windows)) + "\n", "")

        with patch("todo_flow.terminal_tmux.subprocess.run", side_effect=query) as calls:
            for pair in range(25):
                pending = []
                for number in (pair * 2, pair * 2 + 1):
                    folder, slots = self.prepare(number)
                    folders.append(folder)
                    windows.add(f"@{number + 1}")
                    pending.append((number, folder))
                maximum = max(maximum, len(windows) - 1)
                for number, folder in pending:
                    before = (folder / "worker.stdout").read_bytes()
                    receipt = (folder / "terminal-process.json").read_bytes()
                    windows.remove(f"@{number + 1}")
                    self.assertEqual(retire_launch(folder)["status"], "closed")
                    # A replacement adapter/driver can reread a closed receipt.
                    self.assertEqual(retire_launch(folder)["status"], "closed")
                    self.assertEqual((folder / "worker.stdout").read_bytes(), before)
                    self.assertEqual((folder / "terminal-process.json").read_bytes(), receipt)
            self.assertEqual(calls.call_count, 50)
        self.assertEqual(windows, {"@0"})
        self.assertEqual(maximum, 2)
        snapshot = slots.snapshot()
        self.assertEqual(snapshot["limits"], {"concurrency": 2, "idle": 1})
        self.assertEqual(snapshot["max_owned"], 2)
        self.assertEqual(slots.counts(snapshot)["closed"], 50)
        self.assertEqual(len(folders), 50)

    def test_retained_windows_stay_charged_until_observed_absent(self):
        first, slots = self.prepare(0)
        second, _ = self.prepare(1)
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "@0\n@1\n@2\n", ""),
        ):
            self.assertEqual(retire_launch(first)["state"], "quarantined")
            self.assertEqual(retire_launch(second)["state"], "quarantined")
            with self.assertRaises(TerminalCapacityError):
                self.prepare(2)
        self.assertEqual(slots.counts(slots.snapshot())["quarantined"], 2)
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "@0\n", ""),
        ):
            self.assertEqual(retire_launch(first)["state"], "closed")
            self.assertEqual(retire_launch(second)["state"], "closed")

    def test_fifty_delayed_removals_reconcile_before_next_reservation(self):
        windows = {"@0"}
        preserved = {}
        maximum = 0
        deferred = 0

        def query(args, **kwargs):
            self.assertEqual(args[-4:], ["list-windows", "-a", "-F", "#{window_id}"])
            return subprocess.CompletedProcess(args, 0, "\n".join(sorted(windows)) + "\n", "")

        with patch("todo_flow.terminal_tmux.subprocess.run", side_effect=query):
            for pair in range(25):
                pending = []
                for number in (pair * 2, pair * 2 + 1):
                    folder, slots = self.prepare(number)
                    windows.add(f"@{number + 1}")
                    pending.append(folder)
                    for name in ("worker.stdout", "terminal-process.json", "terminal-spec.json"):
                        path = folder / name
                        preserved[path] = path.read_bytes()
                    counts = slots.counts(slots.snapshot())
                    self.assertLessEqual(counts["active"], 2)
                    self.assertEqual(counts["closed"], pair * 2)
                maximum = max(maximum, len(windows) - 1)
                # The receipt exists while the physical windows still exist.
                for folder in pending:
                    self.assertEqual(retire_launch(folder)["state"], "quarantined")
                    deferred += 1
                self.assertEqual(slots.counts(slots.snapshot())["quarantined"], 2)
                # The supervisor exits after retirement returned. The next
                # reservation must discover absence without manual cleanup.
                windows = {"@0"}
            # Exercise the same reconciliation boundary for the final pair,
            # without creating a fifty-first physical terminal.
            reconcile_tmux_terminals(slots)
        snapshot = slots.snapshot()
        self.assertEqual(len(snapshot["slots"]), 50)
        self.assertEqual(slots.counts(snapshot)["closed"], 50)
        self.assertEqual(slots.counts(snapshot)["quarantined"], 0)
        self.assertEqual(snapshot["max_owned"], 2)
        self.assertEqual(maximum, 2)
        self.assertEqual(deferred, 50)
        self.assertEqual(windows, {"@0"})
        for path, content in preserved.items():
            self.assertEqual(path.read_bytes(), content)
        executions = {
            json.loads(path.read_text())["launch_identity"]["execution"]
            for path in preserved
            if path.name == "terminal-spec.json"
        }
        self.assertEqual(len(executions), 50)

    def test_reconciliation_preserves_capacity_when_socket_changes(self):
        first, slots = self.prepare(0)
        second, _ = self.prepare(1)
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "@0\n@1\n@2\n", ""),
        ):
            retire_launch(first)
            retire_launch(second)
        with (
            patch("todo_flow.terminal_tmux.socket_identity", return_value=None),
            patch("todo_flow.terminal_tmux.subprocess.run") as query,
        ):
            with self.assertRaises(TerminalCapacityError):
                self.prepare(2)
            query.assert_not_called()
        self.assertEqual(slots.counts(slots.snapshot())["quarantined"], 2)

    def test_reconciliation_rechecks_process_receipt_before_inventory(self):
        first, slots = self.prepare(0)
        second, _ = self.prepare(1)
        with patch(
            "todo_flow.terminal_tmux.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "@0\n@1\n@2\n", ""),
        ):
            retire_launch(first)
            retire_launch(second)
        for folder in (first, second):
            (folder / "terminal-process.json").write_text(
                json.dumps({"status": "exited", "returncode": 0, "cleanup_confirmed": False})
            )
        with patch("todo_flow.terminal_tmux.subprocess.run") as query:
            with self.assertRaises(TerminalCapacityError):
                self.prepare(2)
            query.assert_not_called()
        self.assertEqual(slots.counts(slots.snapshot())["quarantined"], 2)
