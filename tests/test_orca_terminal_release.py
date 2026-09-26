"""Synthetic CLI envelopes exercise the real Orca dispatch/retirement adapter."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow.launchers import spawn_terminal
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_capacity import reconcile_tmux_terminals
from todo_flow.terminal_release import retire_launch
from todo_flow.terminal_slots import TerminalSlots


class OrcaTerminalReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / "attempt"
        self.folder.mkdir()
        self.inventory = ProcessInventory(self.root, "track", "attempt", "task", 1)
        self.inventory.start()
        self.identity = self.inventory.register()
        self.slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.runtime = "runtime-1"
        self.present = True
        self.ready = True
        self.remove_on_close = True
        self.lose_response = False
        self.killed = False
        self.truncated = False
        self.code = 0
        self.calls = []
        self.terminal = {
            "handle": "term-original",
            "ptyId": "pty-original",
            "incarnationId": "incarnation-original",
            "executionHostId": "local",
            "worktreeId": "worktree-original",
            "tabId": "tab-original",
            "title": "TODO synthetic",
            "connected": False,
            "writable": False,
            "orphaned": False,
            "lastOutputAt": time.time() * 1000,
            "preview": "TODO Flow worker exited: 0",
        }
        self.launcher = {
            "backend": "orca",
            "cli": "synthetic-orca",
            "worktree": "id:worktree-original",
            "repo": str(self.root),
        }
        mocked = patch("subprocess.run", side_effect=self.cli)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.process = spawn_terminal(
            self.launcher,
            [sys.executable, "-c", "pass"],
            str(self.root),
            self.folder,
            self.terminal["title"],
            launch_identity=self.identity,
        )
        self.addCleanup(self.process.close_lease)
        self.gate = LaunchGate(**self.identity)
        with self.gate.launching():
            pass
        self.launch = json.loads((self.folder / "launch.json").read_text())

    def cli(self, argv, **kwargs):
        action = argv[2]
        self.calls.append(action)
        if action == "create":
            self.assertTrue(argv[argv.index("--command") + 1].startswith("exec "))
            result = {"terminal": dict(self.terminal)}
        elif action == "list":
            rows = [dict(self.terminal)] if self.present else []
            result = {
                "terminals": rows,
                "totalCount": len(rows),
                "truncated": self.truncated,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            }
        elif action == "wait":
            result = {
                "wait": {
                    "handle": self.terminal["handle"],
                    "condition": "exit",
                    "satisfied": self.ready,
                    "status": "exited" if self.ready else "running",
                    "exitCode": self.code % 256 if self.ready else None,
                }
            }
        elif action == "show":
            result = {"terminal": dict(self.terminal)}
        elif action == "close":
            self.assertEqual(self.state(), "closing")
            events = json.loads((self.folder / "orca-retirement.json").read_text())
            self.assertTrue(events[-1]["close_intent"])
            self.assertEqual(argv[argv.index("--terminal") + 1], "term-original")
            if self.remove_on_close:
                self.present = False
            if self.lose_response:
                raise subprocess.TimeoutExpired(argv, 10)
            result = {
                "close": {
                    "handle": "term-original",
                    "tabId": "tab-original",
                    "ptyKilled": self.killed,
                }
            }
        else:
            self.fail(f"Unexpected CLI operation: {argv}")
        payload = {"ok": True, "result": result, "_meta": {"runtimeId": self.runtime}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    def finish(self, code=0):
        self.code = code
        event = self.gate._advance(
            self.gate._event(), "cleaning", "Synthetic exit", {"fixture": True}
        )
        self.gate._advance(
            event,
            "confirmed",
            "Synthetic group exit",
            {
                "identity": {k: self.identity[k] for k in ("track", "attempt", "execution")},
                "outcome": "group-exited",
                "proof": "Synthetic supervisor confirmed group exit",
            },
        )
        self.terminal["preview"] = f"TODO Flow worker exited: {code}"
        self.terminal["lastOutputAt"] = time.time() * 1000
        (self.folder / "terminal-process.json").write_text(
            json.dumps(
                {
                    "status": "exited",
                    "returncode": code,
                    "cleanup_confirmed": True,
                    "finished_at": time.time(),
                }
            )
        )
        (self.folder / "stdout.log").write_text("Synthetic worker output\n")
        (self.folder / "stderr.log").write_text("Synthetic diagnostics\n")

    def state(self):
        slot = self.launch["terminal_slot"]["slot"]
        return self.slots.snapshot()["slots"][slot]["history"][-1]["state"]

    def test_creation_identity_and_normal_retirement_preserve_logs(self):
        self.finish()
        recorded = self.launch["terminal"]["_todo_flow"]
        self.assertEqual(recorded["runtimeId"], "runtime-1")
        self.assertEqual(recorded["decision"], "decision-95af607c8a6244a8")
        names = ["stdout.log", "stderr.log", "terminal-process.json", "terminal-spec.json"]
        before = {name: (self.folder / name).read_bytes() for name in names}
        self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(self.calls.count("close"), 1)
        self.assertEqual(self.calls.count("create"), 1)
        self.assertEqual(before, {name: (self.folder / name).read_bytes() for name in names})
        journal = (self.folder / "orca-retirement.json").read_text()
        self.assertIn("ptyKilled", journal)
        self.assertIn("Complete same-runtime inventory", journal)

    def test_error_exit_can_retire_after_group_confirmation(self):
        self.finish(7)
        self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(self.calls.count("close"), 1)

    def test_cancelled_exit_can_retire_after_group_confirmation(self):
        self.finish(130)
        self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(self.calls.count("close"), 1)

    def test_unconfirmed_exit_cannot_reach_orca(self):
        before = list(self.calls)
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.calls, before)
        self.assertEqual(self.state(), "active")

    def test_delayed_pty_exit_reobserves_before_capacity_reservation(self):
        self.finish()
        self.ready = False
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.state(), "active")
        self.assertNotIn("close", self.calls)
        self.ready = True
        reconcile_tmux_terminals(self.slots)
        self.assertEqual(self.state(), "closed")
        self.assertEqual(self.calls.count("close"), 1)

    def test_lost_close_response_reconciles_after_adapter_restart_without_reclose(self):
        self.finish()
        self.lose_response = True
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.state(), "closing")
        self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(self.calls.count("close"), 1)
        self.assertEqual(self.calls.count("create"), 1)
        self.assertIn("error", (self.folder / "orca-retirement.json").read_text())

    def test_pty_killed_does_not_release_a_retained_tab(self):
        self.finish()
        self.killed = True
        self.remove_on_close = False
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.state(), "closing")
        self.assertEqual(self.calls.count("close"), 1)
        self.present = False
        self.assertEqual(retire_launch(self.folder)["status"], "closed")

    def test_runtime_restart_cannot_prove_original_absence(self):
        self.finish()
        self.runtime = "runtime-replacement"
        self.present = False
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertNotEqual(self.state(), "closed")
        self.assertNotIn("close", self.calls)

    def test_truncated_inventory_cannot_prove_absence(self):
        self.finish()
        self.present = False
        self.truncated = True
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.state(), "quarantined")
        self.assertNotIn("close", self.calls)

    def test_changed_incarnation_is_preserved(self):
        self.finish()
        self.terminal["incarnationId"] = "replacement"
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.state(), "quarantined")
        self.assertNotIn("close", self.calls)

    def test_changed_handle_is_preserved(self):
        self.finish()
        self.terminal["handle"] = "term-user"
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertNotIn("close", self.calls)

    def test_observed_user_input_is_preserved(self):
        self.finish()
        self.terminal["lastInputAt"] = time.time() * 1000 + 100
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertNotIn("close", self.calls)

    def test_output_after_exit_marker_is_preserved(self):
        self.finish()
        self.terminal["preview"] += "\nuser output"
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertNotIn("close", self.calls)

    def test_late_output_timestamp_is_preserved(self):
        self.finish()
        self.terminal["lastOutputAt"] += 10000
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertNotIn("close", self.calls)

    def test_changed_claim_cannot_reach_orca(self):
        self.finish()
        with self.inventory.locked():
            value = self.inventory.read()
            value["claim"] = {"task": "replacement", "generation": 2}
            self.inventory.write(value)
        before = list(self.calls)
        self.assertEqual(retire_launch(self.folder)["status"], "preserved")
        self.assertEqual(self.calls, before)
        self.assertNotEqual(self.state(), "closed")
