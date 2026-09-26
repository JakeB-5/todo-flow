from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import verification
from todo_flow.owned_process_group import OwnedProcessGroup
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
        with (
            patch.object(OwnedProcessGroup, "leader_exited", side_effect=KeyboardInterrupt),
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

    def test_parent_exit_cleans_writing_descendants_without_reaping_early(self):
        for inherited_output in (False, True):
            with self.subTest(inherited_output=inherited_output):
                self.identity["track"] += "-next"
                self.real_popen = subprocess.Popen
                self.processes = []
                ready = Path(self.directory) / "ready"
                writes = Path(self.directory) / "writes"
                ready.unlink(missing_ok=True)
                writes.unlink(missing_ok=True)
                child = (
                    "import signal,time\n"
                    "from pathlib import Path\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    f"Path({str(ready)!r}).touch()\n"
                    "while True:\n"
                    f"    with open({str(writes)!r}, 'a') as output: output.write('tick\\n')\n"
                    "    time.sleep(.01)\n"
                )
                redirect = (
                    ""
                    if inherited_output
                    else ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
                )
                parent = (
                    "import subprocess,sys,time\n"
                    "from pathlib import Path\n"
                    f"subprocess.Popen([sys.executable, '-c', {child!r}]{redirect})\n"
                    f"while not Path({str(ready)!r}).exists(): time.sleep(.01)\n"
                    "sys.stdout.write('x' * 262144)\n"
                    "sys.stderr.write('y' * 262144)\n"
                )
                stop = OwnedProcessGroup.stop
                pinned = []

                def check_pin(group):
                    # A zombie must still reserve the leader PID at cleanup.
                    try:
                        pinned.append(group._snapshot()[0])
                        self.assertIsNone(group._proc.returncode)
                    finally:
                        code = stop(group)
                    return code

                with (
                    patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn),
                    patch.object(OwnedProcessGroup, "stop", check_pin),
                ):
                    with self.assertRaisesRegex(
                        verification.VerificationCleanupError, "ownership remains unconfirmed"
                    ):
                        self.run_command(parent, timeout=10)
                self.assertEqual(pinned, [True])
                self.assert_held_and_stopped()
                self.assertFalse(verification.group_running(self.processes[0].pid))
                before = writes.read_bytes()
                time.sleep(0.1)
                self.assertEqual(writes.read_bytes(), before)

    def test_nonzero_exit_preserves_command_diagnostics_and_barrier(self):
        self.real_popen = subprocess.Popen
        with patch("todo_flow.verification.subprocess.Popen", side_effect=self.spawn):
            with self.assertRaisesRegex(RuntimeError, r"failed \(7\): stderr\s+stdout"):
                self.run_command(
                    "import sys; print('stdout'); print('stderr', file=sys.stderr); sys.exit(7)",
                    timeout=5,
                )
        self.assert_held_and_stopped()
