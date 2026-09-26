import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.headless_supervision import headless_process
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.verification import VerificationCleanupError


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class HeadlessSupervisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.identity = {
            "directory": temporary.name,
            "track": "track",
            "attempt": "attempt",
            "execution": "worker",
        }
        self.processes = []
        self.real_popen = subprocess.Popen

    def spawn(self, *args, **kwargs):
        proc = self.real_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            self.processes.append(proc)
            self.addCleanup(self.dispose, proc)
        return proc

    def dispose(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    def launch(self):
        return headless_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            identity=self.identity,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def assert_stopped_and_held(self):
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].poll())
        gate = LaunchGate(**self.identity)
        self.assertEqual(gate.barrier.history()[-1]["state"], "unknown")
        with self.assertRaises(ProcessBarrierError):
            gate.barrier.require_clear()
        with self.assertRaises(ProcessBarrierError):
            gate.cancel_pending()

    def test_prepare_failure_prevents_spawn(self):
        with (
            patch.object(LaunchGate, "prepare", side_effect=ProcessBarrierError("disk failure")),
            patch("todo_flow.headless_supervision.subprocess.Popen") as spawn,
        ):
            with self.assertRaises(ProcessBarrierError):
                with self.launch():
                    self.fail("Failed intent must not reach the body")
        spawn.assert_not_called()

    def test_acknowledgement_and_cleaning_write_failures_still_stop_process(self):
        advance = LaunchGate._advance
        for stage in ("running", "cleaning"):
            with self.subTest(stage=stage):
                self.identity["track"] = stage
                self.processes = []
                reached_body = []

                def fail_stage(gate, event, state, reason, evidence):
                    if state == stage:
                        raise OSError("injected journal failure")
                    return advance(gate, event, state, reason, evidence)

                with (
                    patch(
                        "todo_flow.headless_supervision.subprocess.Popen", side_effect=self.spawn
                    ),
                    patch.object(LaunchGate, "_advance", fail_stage),
                ):
                    with self.assertRaises((OSError, ProcessBarrierError)):
                        with self.launch():
                            reached_body.append(True)
                self.assertEqual(bool(reached_body), stage == "cleaning")
                self.assert_stopped_and_held()

    def test_interruption_stops_process_and_preserves_exception(self):
        with patch("todo_flow.headless_supervision.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaises(KeyboardInterrupt):
                with self.launch():
                    raise KeyboardInterrupt
        self.assert_stopped_and_held()

    def test_successful_body_cannot_clear_unconfirmed_ownership(self):
        with patch("todo_flow.headless_supervision.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaisesRegex(VerificationCleanupError, "ownership remains unconfirmed"):
                with self.launch() as proc:
                    self.assertIsNone(proc.poll())
        self.assert_stopped_and_held()

    def test_cleanup_failure_leaves_durable_hold(self):
        with (
            patch("todo_flow.headless_supervision.subprocess.Popen", side_effect=self.spawn),
            patch(
                "todo_flow.headless_supervision.stop_group",
                side_effect=VerificationCleanupError("inspection failed"),
            ),
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "inspection failed"):
                with self.launch():
                    pass
        gate = LaunchGate(**self.identity)
        self.assertEqual(gate.barrier.history()[-1]["state"], "unknown")
        with self.assertRaises(ProcessBarrierError):
            gate.barrier.require_clear()
        # The fixture owns this still-live direct child and reaps it in cleanup.
        self.assertIsNone(self.processes[0].poll())
