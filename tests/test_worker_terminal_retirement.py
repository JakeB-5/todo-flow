"""Exercise retirement through run_worker, with a synthetic physical backend."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.cleanup import terminal_cleanup
from todo_flow.launchers import TerminalProcess
from todo_flow.process_inventory import note_prepared
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_capacity import accept_terminal, reserve_terminal
from todo_flow.terminal_release import retire_launch
from todo_flow.terminal_retirement import TerminalObservation
from todo_flow.verification import VerificationCleanupError
from todo_flow.worker import run_worker


class WorkerTerminalRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / "attempts" / "attempt"
        self.mode = "normal"
        self.present = True
        self.closes = 0
        self.probes = 0
        self.activity = "incarnation-1:activity-1"
        self.refuse = False
        self.lose_response = False

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value))

    def finish(self, code):
        event = self.gate._advance(
            self.gate._event(), "cleaning", "Synthetic exit", {"fixture": True}
        )
        self.gate._advance(
            event,
            "confirmed",
            "Synthetic group exit",
            {
                "identity": {key: self.identity[key] for key in ("track", "attempt", "execution")},
                "outcome": "group-exited",
                "proof": "Synthetic supervisor confirmed group exit",
            },
        )
        self.write(
            "terminal-process.json",
            {"status": "exited", "returncode": code, "cleanup_confirmed": True},
        )

    def spawn(self, launcher, argv, workspace, folder, title, *, launch_identity):
        self.identity = launch_identity
        reservation = reserve_terminal(launcher, launch_identity, folder)
        self.slots = reservation[0]
        self.gate = LaunchGate.prepare(**launch_identity, backend="synthetic")
        note_prepared(launch_identity)
        with self.gate.launching():
            pass
        self.write("terminal-spec.json", {"launch_identity": launch_identity})
        record = {
            "backend": "synthetic",
            "handle": "physical-1",
            "status": "accepted",
            "terminal_ledger": str(self.slots.path),
        }
        self.lease = accept_terminal(reservation, record, folder)
        record["terminal_slot"] = self.lease
        self.write("launch.json", record)
        self.write("output.json", {"summary": "Synthetic result"})
        self.write("terminal-process.json", {"status": "running", "pid": 0})
        process = TerminalProcess(folder)
        if self.mode in ("normal", "error"):
            self.finish(0 if self.mode == "normal" else 7)
        else:
            original_stop = process.stop

            def stop():
                if self.mode == "unconfirmed":
                    raise VerificationCleanupError("Synthetic group exit is unconfirmed")
                self.finish(130)
                original_stop()

            process.stop = stop
        return process

    def inspect(self, resource):
        self.probes += 1
        return TerminalObservation(
            "idle" if self.present else "absent",
            resource,
            "Complete synthetic inventory",
            self.activity,
        )

    def close(self, observation):
        current = self.slots.snapshot()["slots"][self.lease["slot"]]["history"][-1]
        self.assertEqual(current["state"], "closing")
        self.assertEqual(current["evidence"]["activity_token"], observation.activity_token)
        if self.refuse:
            self.activity = "incarnation-1:user-input-2"
        if observation.activity_token != self.activity:
            return
        self.closes += 1
        self.present = False
        if self.lose_response:
            raise OSError("Synthetic lost close response")

    def execute(self, *, supported=True):
        config = {
            "worker": {"type": "command", "argv": ["synthetic"]},
            "worker_protocol": 2,
            "worker_timeout": -1 if self.mode == "timeout" else 600,
        }

        def heartbeat(pid):
            if self.mode in ("cancel", "unconfirmed"):
                raise RuntimeError("Synthetic cancellation")

        with (
            patch("todo_flow.worker.select_launcher", return_value={"backend": "synthetic"}),
            patch("todo_flow.worker.spawn_terminal", self.spawn),
        ):
            if supported:
                with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
                    return run_worker(
                        config,
                        {"workspace": str(self.root)},
                        {
                            "id": "task",
                            "track": "track",
                            "attempt": "attempt",
                            "generation": 1,
                            "kind": "work",
                        },
                        self.root,
                        heartbeat,
                    )
            return run_worker(
                config,
                {"workspace": str(self.root)},
                {
                    "id": "task",
                    "track": "track",
                    "attempt": "attempt",
                    "generation": 1,
                    "kind": "work",
                },
                self.root,
                heartbeat,
            )

    def state(self):
        return self.slots.snapshot()["slots"][self.lease["slot"]]["history"][-1]["state"]

    def assert_closed_once(self):
        self.assertEqual(self.state(), "closed")
        self.assertEqual(self.closes, 1)
        self.assertFalse(self.present)
        self.assertTrue((self.folder / "output.json").exists())
        self.assertTrue((self.folder / "terminal-process.json").exists())
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            self.assertEqual(terminal_cleanup(self.folder, False)["status"], "closed")
            self.assertEqual(retire_launch(self.folder)["status"], "closed")
        self.assertEqual(self.closes, 1)
        self.assertEqual(self.probes, 2)

    def test_normal_exit_retires_before_return_and_cleanup_does_not_close_again(self):
        self.assertEqual(self.execute()["summary"], "Synthetic result")
        self.assert_closed_once()

    def test_error_exit_retires_before_propagating_worker_error(self):
        self.mode = "error"
        with self.assertRaisesRegex(RuntimeError, "Worker exited 7"):
            self.execute()
        self.assert_closed_once()

    def test_timeout_retires_after_confirmed_stop(self):
        self.mode = "timeout"
        with self.assertRaises(TimeoutError):
            self.execute()
        self.assert_closed_once()

    def test_cancellation_retires_after_confirmed_stop(self):
        self.mode = "cancel"
        with self.assertRaisesRegex(RuntimeError, "Synthetic cancellation"):
            self.execute()
        self.assert_closed_once()

    def test_unconfirmed_stop_never_inspects_or_closes(self):
        self.mode = "unconfirmed"
        with self.assertRaises(VerificationCleanupError):
            self.execute()
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            self.assertEqual(terminal_cleanup(self.folder, False)["status"], "preserved")
        self.assertEqual(self.state(), "active")
        self.assertEqual((self.probes, self.closes), (0, 0))

    def test_unsupported_backend_remains_charged(self):
        self.execute(supported=False)
        self.assertEqual(self.state(), "quarantined")
        self.assertEqual(self.closes, 0)
        self.assertTrue(self.present)

    def test_refused_close_is_not_retried_by_cleanup(self):
        self.refuse = True
        self.execute()
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            self.assertEqual(terminal_cleanup(self.folder, False)["status"], "preserved")
        self.assertEqual(self.state(), "closing")
        self.assertEqual(self.closes, 0)
        self.assertTrue(self.present)

    def test_lost_close_response_preserves_result_and_reconciles_without_reclose(self):
        self.lose_response = True
        self.assertEqual(self.execute()["summary"], "Synthetic result")
        self.assertEqual(self.state(), "closing")
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            self.assertEqual(terminal_cleanup(self.folder, False)["status"], "closed")
        self.assertEqual(self.closes, 1)

    def test_dry_run_does_not_probe_or_mutate_ledger(self):
        self.execute(supported=False)
        before = self.slots.path.read_bytes()
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            self.assertEqual(terminal_cleanup(self.folder, True)["status"], "preserved")
        self.assertEqual(self.slots.path.read_bytes(), before)
        self.assertEqual((self.probes, self.closes), (0, 0))

    def test_misattributed_launch_cannot_reach_adapter(self):
        self.execute(supported=False)
        record = json.loads((self.folder / "launch.json").read_text())
        record["terminal_slot"]["owner"]["execution"] = "another-execution"
        self.write("launch.json", record)
        with patch("todo_flow.terminal_release.terminal_adapter", return_value=self):
            report = terminal_cleanup(self.folder, False)
        self.assertEqual(report["status"], "preserved")
        self.assertIn("identity", report["reason"])
        self.assertEqual((self.probes, self.closes), (0, 0))
