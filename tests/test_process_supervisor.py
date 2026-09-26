import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import process_supervisor
from todo_flow.owned_process_group import OwnedProcessGroup
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class ProcessSupervisorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.identity = {
            "directory": str(self.directory),
            "track": "track",
            "attempt": "attempt",
            "execution": "execution",
        }
        self.gate = LaunchGate.prepare(**self.identity, backend="supervisor")

    def dispose(self, proc):
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def start(self, code, timeout=20):
        read_fd, write_fd = os.pipe()
        try:
            # This separate process owns the sole driver lease. Killing it tests
            # kernel EOF delivery; the test retains the supervisor Popen only
            # to reap its fixture. Runtime caller integration is separate.
            driver = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                pass_fds=(write_fd,),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.addCleanup(self.dispose, driver)
            specification = {
                "argv": [sys.executable, "-c", code],
                "identity": self.identity,
                "lease_fd": read_fd,
                "cwd": str(self.directory),
                "timeout": timeout,
            }
            supervisor = subprocess.Popen(
                [sys.executable, process_supervisor.__file__, json.dumps(specification)],
                pass_fds=(read_fd,),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.addCleanup(self.dispose, supervisor)
        finally:
            os.close(read_fd)
            os.close(write_fd)
        return driver, supervisor

    def await_file(self, path):
        deadline = time.monotonic() + 10
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail(f"Fixture did not become ready: {path}")
            time.sleep(0.02)

    def owner(self, code):
        with self.gate.launching():
            owner = OwnedProcessGroup(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.addCleanup(owner.stop)
        return owner

    def test_driver_sigkill_cleans_term_ignoring_descendant_and_preserves_control(self):
        ready = self.directory / "ready"
        writes = self.directory / "writes"
        child = (
            "import signal,time\n"
            "from pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"Path({str(writes)!r}).write_text('started')\n"
            f"Path({str(ready)!r}).touch()\n"
            "while True:\n"
            f"    with open({str(writes)!r}, 'a') as output: output.write('x')\n"
            "    time.sleep(.01)\n"
        )
        parent = (
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "time.sleep(60)\n"
        )
        control = OwnedProcessGroup(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(control.stop)
        driver, supervisor = self.start(parent)
        self.await_file(ready)
        with self.assertRaises(ProcessBarrierError):
            self.gate.barrier.require_clear()
        driver.kill()
        self.assertEqual(driver.wait(timeout=5), -signal.SIGKILL)
        self.assertEqual(supervisor.wait(timeout=10), 125)
        # A fresh reader can use the per-execution proof without signalling.
        reopened = LaunchGate(**self.identity)
        reopened.barrier.require_clear()
        event = reopened._event()
        self.assertEqual(event["evidence"]["completion"], "driver-disconnected")
        self.assertEqual(event["evidence"]["outcome"], "group-exited")
        self.assertEqual(event["evidence"]["supervisor_pid"], supervisor.pid)
        before = writes.read_bytes()
        time.sleep(0.15)
        self.assertEqual(writes.read_bytes(), before)
        self.assertFalse(control.leader_exited())

    def test_timeout_records_cleanup_without_recording_command_success(self):
        _, supervisor = self.start("import time; time.sleep(60)", timeout=0.1)
        self.assertEqual(supervisor.wait(timeout=10), 124)
        self.gate.barrier.require_clear()
        event = self.gate._event()
        self.assertEqual(event["evidence"]["completion"], "timeout")
        self.assertLess(event["evidence"]["returncode"], 0)

    def test_nonzero_command_exit_is_preserved(self):
        _, supervisor = self.start("raise SystemExit(7)")
        self.assertEqual(supervisor.wait(timeout=10), 7)
        self.gate.barrier.require_clear()
        self.assertEqual(self.gate._event()["evidence"]["returncode"], 7)

    def test_cancelled_dispatch_never_spawns(self):
        marker = self.directory / "unexpected"
        self.gate.cancel_pending()
        _, supervisor = self.start(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        self.assertNotEqual(supervisor.wait(timeout=10), 0)
        self.assertFalse(marker.exists())
        self.assertEqual(self.gate._event()["evidence"]["outcome"], "not-spawned")

    def test_failed_signal_is_retried_by_same_owner_before_confirmation(self):
        owner = self.owner("import time; time.sleep(60)")
        send = os.killpg
        attempts = []

        def deny_once(pid, sig):
            attempts.append((pid, sig))
            if len(attempts) == 1:
                raise PermissionError("injected denial")
            self.assertIsNone(owner._proc.returncode)
            return send(pid, sig)

        with patch("todo_flow.owned_process_group.os.killpg", side_effect=deny_once):
            self.assertLess(process_supervisor._finish(self.gate, owner, "signal"), 0)
        events = self.gate.barrier.history()
        self.assertIn("unknown", [event["state"] for event in events])
        self.assertEqual(events[-1]["state"], "confirmed")
        self.assertTrue(all(pid == owner.pid for pid, _ in attempts))

    def test_journal_failure_still_cleans_but_cannot_confirm(self):
        owner = self.owner("import time; time.sleep(60)")
        advance = LaunchGate._advance

        def deny_cleaning(gate, event, state, reason, evidence):
            if state == "cleaning":
                raise OSError("injected journal failure")
            return advance(gate, event, state, reason, evidence)

        with patch.object(LaunchGate, "_advance", deny_cleaning):
            with self.assertRaisesRegex(OSError, "journal failure"):
                process_supervisor._finish(self.gate, owner, "signal")
        self.assertIsNotNone(owner._proc.returncode)
        with self.assertRaises(ProcessBarrierError):
            LaunchGate(**self.identity).barrier.require_clear()
        self.assertNotIn("confirmed", [event["state"] for event in self.gate.barrier.history()])
