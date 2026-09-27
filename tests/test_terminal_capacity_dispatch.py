"""Exercise capacity fencing through the real terminal dispatch entry point."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.launchers import select_launcher, spawn_terminal
from todo_flow.process_inventory import ProcessInventory
from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots


class TerminalCapacityDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def slots(self):
        return TerminalSlots(self.root, concurrency=2, idle_limit=1)

    def identity(self, number):
        # Each request belongs to an independent track. A single track's process
        # barrier intentionally rejects another execution while its first launch
        # is unresolved; this fixture exercises the shared terminal limit instead.
        inventory = ProcessInventory(self.root, f"fixture-{number}", f"attempt-{number}", "task", 7)
        inventory.start()
        return inventory.register()

    def dispatch(self, number, launcher, identity=None):
        folder = self.root / f"attempt-{number}"
        folder.mkdir(exist_ok=True)
        return spawn_terminal(
            launcher,
            [sys.executable, "-c", "pass"],
            str(self.root),
            folder,
            "synthetic",
            launch_identity=identity or self.identity(number),
        )

    def test_custom_dispatch_stops_at_shared_limit_across_fifty_requests(self):
        launcher = {"backend": "terminal", "argv": ["synthetic", "{command}"]}
        accepted = []

        def create(*args, **kwargs):
            snapshot = self.slots().snapshot()
            counts = self.slots().counts(snapshot)
            self.assertEqual(counts["reserved"], 1)
            self.assertLessEqual(counts["active"] + counts["reserved"], 2)
            accepted.append(args[0])
            return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

        with patch("todo_flow.launchers.subprocess.run", side_effect=create):
            for number in range(50):
                if number < 2:
                    process = self.dispatch(number, launcher)
                    process.close_lease()
                else:
                    with self.assertRaises(TerminalCapacityError):
                        self.dispatch(number, launcher)
                    self.assertFalse((self.root / f"attempt-{number}/terminal-spec.json").exists())
        self.assertEqual(len(accepted), 2)
        snapshot = self.slots().snapshot()
        self.assertEqual(snapshot["max_owned"], 2)
        self.assertEqual(self.slots().counts(snapshot)["active"], 2)
        owners = [row["history"][0]["owner"] for row in snapshot["slots"].values()]
        self.assertEqual({owner["track"] for owner in owners}, {"fixture-0", "fixture-1"})
        for owner in owners:
            self.assertEqual(owner["generation"], 7)
            self.assertEqual(owner["task"], "task")
        for number in range(2):
            launch = json.loads((self.root / f"attempt-{number}/launch.json").read_text())
            self.assertEqual(launch["terminal_slot"]["state"], "active")
            self.assertEqual(launch["terminal_ledger"], str(self.slots().path))

    def test_orca_response_loss_remains_charged_and_blocks_new_dispatch(self):
        launcher = {
            "backend": "orca",
            "cli": "synthetic",
            "worktree": "id:synthetic",
            "repo": str(self.root),
        }
        with patch(
            "todo_flow.launchers.orca_result",
            side_effect=subprocess.TimeoutExpired("synthetic", 10),
        ) as create:
            for number in range(2):
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.dispatch(number, launcher)
            with self.assertRaises(TerminalCapacityError):
                self.dispatch(2, launcher)
            self.assertEqual(create.call_count, 2)
        self.assertEqual(self.slots().counts(self.slots().snapshot())["reserved"], 2)
        for number in range(2):
            folder = self.root / f"attempt-{number}"
            self.assertTrue((folder / "terminal-cancelled").exists())
            self.assertEqual(
                json.loads((folder / "launch.json").read_text())["status"], "unconfirmed"
            )

    def test_tmux_duplicate_execution_is_rejected_before_second_create(self):
        launcher = {"backend": "tmux", "socket": "synthetic"}
        identity = self.identity(0)
        with patch(
            "todo_flow.launchers.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="@1\n", stderr=""),
        ) as create:
            process = self.dispatch(0, launcher, identity)
            process.close_lease()
            with self.assertRaises(TerminalCapacityError):
                self.dispatch(0, launcher, identity)
            self.assertEqual(create.call_count, 1)
        snapshot = self.slots().snapshot()
        event = next(iter(snapshot["slots"].values()))["history"][-1]
        self.assertEqual(event["resource"]["handle"], "@1")

    def test_sealed_inventory_cannot_create_a_terminal(self):
        identity = self.identity(0)
        ProcessInventory(self.root, "fixture-0", "attempt-0", "task", 7).seal()
        with patch("todo_flow.launchers.subprocess.run") as create:
            with self.assertRaises(TerminalCapacityError):
                self.dispatch(0, {"backend": "terminal", "argv": ["{command}"]}, identity)
            create.assert_not_called()
        self.assertFalse(self.slots().path.exists())

    def test_headless_has_no_capacity_configuration_or_backend_probe(self):
        with patch("todo_flow.launchers.orca_result") as probe:
            self.assertEqual(
                select_launcher({"worker_launcher": "headless"}, str(self.root)),
                {"backend": "headless"},
            )
            probe.assert_not_called()
        self.assertFalse(self.slots().path.exists())
