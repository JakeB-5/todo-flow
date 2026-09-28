"""Verification uses an independently surviving owner and durable receipts."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import verification, verification_logs
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Conflict
from todo_flow.supervised_process import SupervisedProcess


class SupervisedVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.identity = dict(
            directory=str(self.directory), track="track", attempt="attempt", execution="verify"
        )

    def run_command(self, code="print('done')", timeout=5):
        return verification.run(
            [sys.executable, "-c", code], self.directory, timeout, launch_identity=self.identity
        )

    def test_prepare_failure_prevents_spawn(self):
        with (
            patch.object(LaunchGate, "prepare", side_effect=ProcessBarrierError("disk failure")),
            patch("todo_flow.supervised_process.subprocess.Popen") as spawn,
        ):
            with self.assertRaises(ProcessBarrierError):
                self.run_command()
            spawn.assert_not_called()

    def test_success_requires_durable_ownership_confirmation(self):
        self.assertEqual(self.run_command(), "done")
        gate = LaunchGate(**self.identity)
        gate.barrier.require_clear()
        self.assertEqual(gate._event()["evidence"]["outcome"], "group-exited")
        with self.assertRaises(ProcessBarrierError):
            with gate.launching():
                self.fail("A confirmed execution cannot be launched again")

    def test_null_timeout_completes_with_durable_cleanup_and_logs(self):
        self.assertEqual(self.run_command(timeout=None), "done")
        gate = LaunchGate(**self.identity)
        gate.barrier.require_clear()
        event = gate._event()
        self.assertEqual(event["state"], "confirmed")
        self.assertEqual(event["evidence"]["completion"], "leader-exited")
        self.assertEqual(event["evidence"]["outcome"], "group-exited")
        self.assertEqual(event["evidence"]["returncode"], 0)
        logs = verification_logs.describe(
            self.directory, verification_logs.reference(self.identity)
        )
        self.assertTrue(logs["complete"])
        self.assertEqual(logs["streams"]["stdout"]["text"], "done\n")

    def test_null_timeout_check_cancellation_stops_running_process(self):
        marker = self.directory / "verifier.pid"
        ready = self.directory / "ready"
        original = Conflict("verification claim cancelled")
        gate = LaunchGate(**self.identity)
        pid = None
        deadline = time.monotonic() + 5

        def check():
            nonlocal pid
            if ready.exists():
                pid = int(marker.read_text())
                os.kill(pid, 0)
                with self.assertRaises(ProcessBarrierError):
                    gate.barrier.require_clear()
                raise original
            if time.monotonic() >= deadline:
                self.fail("Verifier did not become ready for cancellation")

        code = (
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); "
            "print('started', flush=True); "
            f"Path({str(ready)!r}).touch(); time.sleep(60)"
        )
        with self.assertRaises(Conflict) as raised:
            verification.run(
                [sys.executable, "-c", code],
                self.directory,
                None,
                launch_identity=self.identity,
                check=check,
            )
        self.assertIs(raised.exception, original)
        self.assertIsNotNone(pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        gate.barrier.require_clear()
        event = gate._event()
        self.assertEqual(event["state"], "confirmed")
        self.assertEqual(event["evidence"]["completion"], "driver-disconnected")
        self.assertEqual(event["evidence"]["outcome"], "group-exited")
        logs = verification_logs.describe(
            self.directory, verification_logs.reference(self.identity)
        )
        self.assertFalse(logs["complete"])
        self.assertEqual(logs["streams"]["stdout"]["text"], "started\n")

    def test_timeout_cleans_process_and_confirms_ownership(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_command("import time; time.sleep(60)", 0.1)
        gate = LaunchGate(**self.identity)
        gate.barrier.require_clear()
        self.assertEqual(gate._event()["evidence"]["completion"], "timeout")

    def test_interrupt_cleans_process_and_preserves_original_exception(self):
        poll = SupervisedProcess.poll
        interrupted = False

        def interrupt_once(process):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return poll(process)

        with patch.object(SupervisedProcess, "poll", interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                self.run_command("import time; time.sleep(60)")
        LaunchGate(**self.identity).barrier.require_clear()

    def test_nonzero_exit_preserves_command_diagnostics_and_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "failure-detail"):
            self.run_command("import sys; print('failure-detail',file=sys.stderr); sys.exit(7)")
        LaunchGate(**self.identity).barrier.require_clear()

    def test_supervisor_without_confirmation_cannot_return_success(self):
        real_spawn = subprocess.Popen

        def spawn(args, **kwargs):
            return real_spawn([sys.executable, "-c", "pass"], **kwargs)

        with patch("todo_flow.supervised_process.subprocess.Popen", spawn):
            with self.assertRaises(verification.VerificationCleanupError):
                self.run_command()
        with self.assertRaises(ProcessBarrierError):
            LaunchGate(**self.identity).barrier.require_clear()

    def test_journal_failure_after_spawn_still_stops_the_owned_process(self):
        real_spawn = subprocess.Popen
        source = str(Path(verification.__file__).resolve().parents[1])
        for stage in ("running", "cleaning"):
            with self.subTest(stage=stage):
                self.identity["track"] = stage
                marker = self.directory / (stage + ".pid")
                bootstrap = """import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from todo_flow import process_supervisor
original=process_supervisor.LaunchGate._advance
spec=json.loads(sys.argv[2]); stage=sys.argv[3]; marker=Path(sys.argv[4])
def advance(self,event,state,reason,evidence):
    if state==stage:
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
        raise OSError('injected journal failure')
    return original(self,event,state,reason,evidence)
process_supervisor.LaunchGate._advance=advance
raise SystemExit(process_supervisor.supervise(**spec))
"""

                def spawn(args, **kwargs):
                    return real_spawn(
                        [sys.executable, "-c", bootstrap, source, args[-1], stage, str(marker)],
                        **kwargs,
                    )

                with patch("todo_flow.supervised_process.subprocess.Popen", spawn):
                    with self.assertRaises((RuntimeError, verification.VerificationCleanupError)):
                        self.run_command(
                            f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(.1)"
                        )
                self.assertTrue(marker.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(marker.read_text()), 0)
                gate = LaunchGate(**self.identity)
                if stage == "cleaning":
                    with self.assertRaises(ProcessBarrierError):
                        gate.barrier.require_clear()
                else:
                    gate.barrier.require_clear()

    def test_parent_exit_cleans_writing_descendants_with_open_or_closed_output(self):
        for inherited_output in (False, True):
            with self.subTest(inherited_output=inherited_output):
                self.identity["track"] = str(inherited_output)
                ready, writes = self.directory / "ready", self.directory / "writes"
                ready.unlink(missing_ok=True)
                writes.unlink(missing_ok=True)
                child = f"""import signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path({str(ready)!r}).touch()
while True:
    with open({str(writes)!r},'a') as output: output.write('tick')
    time.sleep(.01)
"""
                redirect = (
                    ""
                    if inherited_output
                    else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                )
                parent = f"""import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})
while not Path({str(ready)!r}).exists(): time.sleep(.01)
sys.stdout.write('x'*262144)
sys.stderr.write('y'*262144)
"""
                with self.assertRaisesRegex(RuntimeError, "left background processes"):
                    self.run_command(parent)
                LaunchGate(**self.identity).barrier.require_clear()
                before = writes.read_bytes()
                time.sleep(0.1)
                self.assertEqual(before, writes.read_bytes())
