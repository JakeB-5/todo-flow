import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import verification
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class SupervisedVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.identity = {
            "directory": self.directory,
            "track": "track",
            "attempt": "attempt",
            "execution": "verification",
        }
        self.processes = []

    def spawn(self, *args, **kwargs):
        proc = self.real_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            self.processes.append(proc)
            self.addCleanup(self.dispose, proc)
        return proc

    def dispose(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)

    def run_command(self, code="import time; time.sleep(60)", timeout=0.125):
        return verification.run(
            [sys.executable, "-c", code],
            self.directory,
            timeout,
            launch_identity=self.identity,
        )

    def assert_held_and_stopped(self):
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].poll())
        gate = LaunchGate(**self.identity)
        with self.assertRaises(ProcessBarrierError):
            gate.barrier.require_clear()
        with self.assertRaises(ProcessBarrierError):
            gate.cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            with gate.launching():
                self.fail("Consumed verification permit reached another spawn")
        self.assertEqual(gate.barrier.history()[-1]["state"], "unknown")

    def test_prepare_failure_prevents_spawn(self):
        with (
            patch.object(LaunchGate, "prepare", side_effect=ProcessBarrierError("disk failure")),
            patch("todo_flow.verification.subprocess.Popen") as spawn,
        ):
            with self.assertRaises(ProcessBarrierError):
                self.run_command()
        spawn.assert_not_called()

    def test_journal_failure_after_spawn_still_stops_live_process(self):
        advance = LaunchGate._advance
        self.real_popen = subprocess.Popen
        for stage in ("running", "cleaning"):
            with self.subTest(stage=stage):
                self.identity["execution"] = stage
                self.processes = []

                def fail_stage(gate, event, state, reason, evidence):
                    if state == stage:
                        raise OSError("injected journal failure")
                    return advance(gate, event, state, reason, evidence)

                with (
                    patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn),
                    patch.object(LaunchGate, "_advance", fail_stage),
                ):
                    with self.assertRaises((OSError, ProcessBarrierError)):
                        self.run_command()
                self.assert_held_and_stopped()
                # Each unresolved execution blocks its track, so use another
                # track for the next independent failure fixture.
                self.identity["track"] += "-next"

    def test_timeout_cleans_process_but_does_not_confirm_ownership(self):
        self.real_popen = subprocess.Popen
        with patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_command()
        self.assert_held_and_stopped()

    def test_interrupt_cleans_process_but_does_not_confirm_ownership(self):
        self.real_popen = subprocess.Popen
        communicate = subprocess.Popen.communicate

        def interrupt_once(proc, *args, **kwargs):
            if kwargs.get("timeout") == 0.125:
                raise KeyboardInterrupt
            return communicate(proc, *args, **kwargs)

        with (
            patch.object(self.real_popen, "communicate", interrupt_once),
            patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.run_command()
        self.assert_held_and_stopped()

    def test_successful_command_cannot_create_success_without_ownership_proof(self):
        self.real_popen = subprocess.Popen
        with patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaisesRegex(
                verification.VerificationCleanupError, "ownership remains unconfirmed"
            ):
                self.run_command(code="print('done')", timeout=5)
        self.assert_held_and_stopped()
