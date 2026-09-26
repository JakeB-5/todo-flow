"""Legacy and interrupted evidence must fence the real terminal dispatch path."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from todo_flow.launchers import spawn_terminal
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots
from todo_flow.worker import run_worker


class TerminalLegacyCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.launcher = {"backend": "terminal", "argv": ["synthetic", "{command}"]}

    def slots(self):
        return TerminalSlots(self.root, concurrency=2, idle_limit=1)

    def folder(self, name):
        folder = self.root / "attempts" / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def identity(self, name):
        inventory = ProcessInventory(self.root, "track-" + name, name, "task", 1)
        inventory.start()
        return inventory.register()

    def dispatch(self, name, identity=None):
        return spawn_terminal(
            self.launcher,
            [sys.executable, "-c", "pass"],
            str(self.root),
            self.folder(name),
            "synthetic",
            launch_identity=identity or self.identity(name),
        )

    def legacy(self, name, status="accepted"):
        path = self.folder(name) / "launch.json"
        path.write_text(
            json.dumps({"backend": "terminal", "status": status, "handle": name})
        )
        return path

    def assert_blocked(self, name, evidence):
        with patch("todo_flow.launchers.subprocess.run") as create:
            with self.assertRaises(TerminalCapacityError) as raised:
                self.dispatch(name)
            create.assert_not_called()
        message = str(raised.exception)
        self.assertIn(str(evidence), message)
        self.assertIn(str(self.slots().path), message)
        self.assertIn("physical backend inventory", message)
        self.assertIn("current ownership", message)
        self.assertIn("Process exit", message)
        self.assertFalse((self.folder(name) / "terminal-spec.json").exists())

    def test_missing_and_empty_ledgers_block_three_legacy_tabs_after_restart(self):
        originals = {}
        for number in range(3):
            path = self.legacy(f"legacy-{number}")
            originals[path] = path.read_bytes()
        evidence = next(iter(originals))
        self.assert_blocked("first", evidence)
        self.assertFalse(self.slots().path.exists())
        self.assert_blocked("second", evidence)
        # A fresh driver reading an existing empty ledger must run the same scan.
        slots = self.slots()
        with slots.locked():
            slots.write(slots.read())
        ledger = slots.path.read_bytes()
        self.assert_blocked("after-restart", evidence)
        self.assertEqual(self.slots().path.read_bytes(), ledger)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_partial_ledger_detects_a_missing_launch_without_double_counting(self):
        with patch(
            "todo_flow.launchers.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="one", stderr=""),
        ) as create:
            process = self.dispatch("known")
            process.close_lease()
            create.assert_called_once()
        before = self.slots().path.read_bytes()
        missing = self.legacy("missing")
        self.assert_blocked("next", missing)
        self.assert_blocked("restarted", missing)
        self.assertEqual(self.slots().path.read_bytes(), before)
        self.assertEqual(len(self.slots().snapshot()["slots"]), 1)

    def test_launching_unconfirmed_and_damaged_launch_evidence_block(self):
        path = self.legacy("legacy")
        for number, content in enumerate(
            [
                json.dumps({"backend": "terminal", "status": "launching"}),
                json.dumps({"backend": "orca", "status": "unconfirmed"}),
                "{",
                "[]",
            ]
        ):
            with self.subTest(content=content):
                path.write_text(content)
                self.assert_blocked(f"request-{number}", path)
                self.assertEqual(path.read_text(), content)

    def test_process_exit_and_cleanup_reports_do_not_excuse_legacy_tabs(self):
        path = self.legacy("legacy")
        (path.parent / "terminal-process.json").write_text(
            json.dumps({"status": "exited", "returncode": 0, "cleanup_confirmed": True})
        )
        (path.parent / "terminal-retirement.json").write_text(
            json.dumps({"status": "closed", "cleanup_confirmed": True})
        )
        self.assert_blocked("next", path)

    def test_orphan_specification_and_process_receipt_block(self):
        folder = self.folder("legacy")
        spec = folder / "terminal-spec.json"
        spec.write_text(json.dumps({"launch_identity": self.identity("legacy")}))
        self.assert_blocked("spec-only", spec)
        spec.unlink()
        receipt = folder / "terminal-process.json"
        receipt.write_text(json.dumps({"status": "exited", "cleanup_confirmed": True}))
        self.assert_blocked("receipt-only", receipt)

    def test_execution_intent_survives_missing_launch_and_ledger(self):
        identity = self.identity("legacy")
        gate = LaunchGate.prepare(**identity, backend="terminal")
        ProcessInventory(self.root, identity["track"], identity["attempt"]).prepared(
            identity["execution"]
        )
        self.assert_blocked("next", gate.barrier.path)
        self.assert_blocked("restarted", gate.barrier.path)

    def test_prepared_inventory_without_backend_attribution_blocks(self):
        identity = self.identity("legacy")
        inventory = ProcessInventory(self.root, identity["track"], identity["attempt"])
        inventory.prepared(identity["execution"])
        self.assert_blocked("next", inventory.path)

    def test_damaged_journal_and_inventory_block(self):
        identity = self.identity("legacy")
        gate = LaunchGate.prepare(**identity, backend="terminal")
        gate.barrier.path.write_text("{")
        self.assert_blocked("journal", gate.barrier.path)
        gate.barrier.path.unlink()
        inventory = ProcessInventory(self.root, identity["track"], identity["attempt"])
        inventory.path.write_text("{")
        self.assert_blocked("inventory", inventory.path)

    def test_known_launches_are_counted_once_on_repeated_dispatch(self):
        with patch(
            "todo_flow.launchers.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="same-text", stderr=""),
        ) as create:
            for name in ("first", "second"):
                process = self.dispatch(name)
                process.close_lease()
            with self.assertRaises(TerminalCapacityError):
                self.dispatch("third")
            self.assertEqual(create.call_count, 2)
        snapshot = self.slots().snapshot()
        self.assertEqual(len(snapshot["slots"]), 2)
        self.assertEqual(snapshot["max_owned"], 2)
        self.assertEqual(self.slots().counts(snapshot)["active"], 2)

    def test_unconfirmed_modern_lease_is_charged_once(self):
        launcher = {"backend": "orca", "cli": "synthetic", "worktree": "id:test"}
        with patch(
            "todo_flow.launchers.orca_result",
            side_effect=subprocess.TimeoutExpired("synthetic", 10),
        ) as create:
            for name in ("first", "second"):
                with self.assertRaises(subprocess.TimeoutExpired):
                    spawn_terminal(
                        launcher,
                        [sys.executable, "-c", "pass"],
                        str(self.root),
                        self.folder(name),
                        "synthetic",
                        launch_identity=self.identity(name),
                    )
            self.assertEqual(create.call_count, 2)
        with patch("todo_flow.launchers.subprocess.run") as create:
            with self.assertRaises(TerminalCapacityError):
                self.dispatch("third")
            create.assert_not_called()
        self.assertEqual(self.slots().counts(self.slots().snapshot())["reserved"], 2)

    def test_concurrent_legacy_requests_cannot_bypass_recovery(self):
        evidence = self.legacy("legacy")
        barrier = threading.Barrier(4, timeout=10)

        def request(number):
            barrier.wait()
            try:
                self.dispatch(f"concurrent-{number}")
            except TerminalCapacityError as error:
                return str(error)
            self.fail("Unaccounted legacy resource admitted a dispatch")

        with patch("todo_flow.launchers.subprocess.run") as create:
            with ThreadPoolExecutor(max_workers=4) as pool:
                messages = list(pool.map(request, range(4)))
            create.assert_not_called()
        self.assertTrue(all(str(evidence) in message for message in messages))
        self.assertFalse(self.slots().path.exists())

    def test_concurrent_clean_requests_share_the_capacity_lock(self):
        barrier = threading.Barrier(4, timeout=10)

        def request(number):
            barrier.wait()
            try:
                process = self.dispatch(f"concurrent-{number}")
            except TerminalCapacityError:
                return False
            process.close_lease()
            return True

        with patch(
            "todo_flow.launchers.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="synthetic", stderr=""),
        ) as create:
            with ThreadPoolExecutor(max_workers=4) as pool:
                accepted = list(pool.map(request, range(4)))
            self.assertEqual(create.call_count, 2)
        self.assertEqual(sum(accepted), 2)
        self.assertEqual(self.slots().snapshot()["max_owned"], 2)

    def test_headless_worker_runs_even_with_unaccounted_visible_resources(self):
        self.legacy("legacy")
        config = {
            "worker": {
                "type": "command",
                "argv": [sys.executable, "-c", "print('{\"summary\": \"synthetic\"}')"],
            },
            "worker_protocol": 2,
            "worker_launcher": "headless",
            "worker_timeout": 10,
        }
        task = {
            "id": "headless-task",
            "track": "headless-track",
            "attempt": "headless-attempt",
            "generation": 1,
            "kind": "work",
        }
        with patch("todo_flow.worker.spawn_terminal") as create:
            run_worker(config, {"workspace": str(self.root)}, task, self.root, lambda pid: None)
            create.assert_not_called()
        self.assertFalse(self.slots().path.exists())
        launch = json.loads((self.folder(task["attempt"]) / "launch.json").read_text())
        self.assertEqual(launch["backend"], "headless")

    def test_prior_headless_execution_does_not_consume_visible_capacity(self):
        identity = self.identity("headless-history")
        LaunchGate.prepare(**identity, backend="supervised")
        inventory = ProcessInventory(self.root, identity["track"], identity["attempt"])
        inventory.prepared(identity["execution"])
        (self.folder("headless-history") / "launch.json").write_text(
            json.dumps({"backend": "headless"})
        )
        with patch(
            "todo_flow.launchers.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="synthetic", stderr=""),
        ) as create:
            process = self.dispatch("visible")
            process.close_lease()
            create.assert_called_once()
        self.assertEqual(len(self.slots().snapshot()["slots"]), 1)
