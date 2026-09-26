from pathlib import Path
import subprocess
import sys
import tempfile
import time
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
            patch("todo_flow.owned_process_group.subprocess.Popen") as spawn,
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
                    patch("todo_flow.owned_process_group.subprocess.Popen", side_effect=self.spawn),
                    patch.object(LaunchGate, "_advance", fail_stage),
                ):
                    with self.assertRaises((OSError, ProcessBarrierError)):
                        with self.launch():
                            reached_body.append(True)
                self.assertEqual(bool(reached_body), stage == "cleaning")
                self.assert_stopped_and_held()

    def test_interruption_stops_process_and_preserves_exception(self):
        with patch("todo_flow.owned_process_group.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaises(KeyboardInterrupt):
                with self.launch():
                    raise KeyboardInterrupt
        self.assert_stopped_and_held()

    def test_successful_body_cannot_clear_unconfirmed_ownership(self):
        with patch("todo_flow.owned_process_group.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaisesRegex(VerificationCleanupError, "ownership remains unconfirmed"):
                with self.launch() as proc:
                    self.assertFalse(proc.leader_exited())
        self.assert_stopped_and_held()

    def test_cleanup_failure_leaves_durable_hold(self):
        with (
            patch("todo_flow.owned_process_group.subprocess.Popen", side_effect=self.spawn),
            patch(
                "todo_flow.headless_supervision.OwnedProcessGroup.stop",
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

    def test_exited_leader_is_retained_until_term_ignoring_child_stops(self):
        ready = Path(self.identity["directory"]) / "ready"
        output = ready.with_name("output")
        child = (
            "import pathlib,signal,sys,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "p=pathlib.Path(sys.argv[2]); p.write_text('started'); "
            "pathlib.Path(sys.argv[1]).touch(); "
            "\nwhile True:\n with p.open('a') as f: f.write('x')\n time.sleep(.02)"
        )
        parent = (
            "import pathlib,subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
            "p=pathlib.Path(sys.argv[2]); "
            "\nwhile not p.exists(): time.sleep(.01)"
        )
        with patch("todo_flow.owned_process_group.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaisesRegex(VerificationCleanupError, "ownership remains unconfirmed"):
                with headless_process(
                    [sys.executable, "-c", parent, child, str(ready), str(output)],
                    identity=self.identity,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ) as group:
                    deadline = time.monotonic() + 5
                    while not group.leader_exited():
                        if time.monotonic() >= deadline:
                            self.fail("Leader did not exit")
                        time.sleep(0.01)
                    self.assertTrue(ready.exists())
                    # Do not poll: the unreaped leader reserves the group ID.
                    self.assertIsNone(self.processes[0].returncode)
        self.assert_stopped_and_held()
        final_output = output.read_bytes()
        time.sleep(0.15)
        self.assertEqual(output.read_bytes(), final_output)
