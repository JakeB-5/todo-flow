import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow.owned_process_group import OwnedProcessGroup
from todo_flow.verification import VerificationCleanupError


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class OwnedProcessGroupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def start(self, code, *args):
        group = OwnedProcessGroup(
            [sys.executable, "-c", code, *map(str, args)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(group.stop)
        return group

    def await_exit(self, group):
        deadline = time.monotonic() + 5
        while not group.leader_exited():
            if time.monotonic() >= deadline:
                self.fail("Leader did not exit")
            time.sleep(0.01)

    def test_zombie_only_group_is_reaped_without_signals(self):
        group = self.start("raise SystemExit(7)")
        self.await_exit(group)
        self.assertIsNone(group._proc.returncode)
        with patch("todo_flow.owned_process_group.os.killpg") as send:
            self.assertEqual(group.stop(), 7)
        send.assert_not_called()
        self.assertEqual(group._proc.returncode, 7)

    def test_exited_leader_pins_group_until_term_ignoring_child_stops(self):
        ready = self.directory / "ready"
        output = self.directory / "output"
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
        group = self.start(parent, child, ready, output)
        control = self.start("import time; time.sleep(60)")
        self.await_exit(group)
        self.assertTrue(ready.exists())
        with patch("todo_flow.owned_process_group.os.killpg", wraps=os.killpg) as send:
            self.assertEqual(group.stop(), 0)
        self.assertEqual(
            [call.args for call in send.call_args_list],
            [(group.pid, signal.SIGTERM), (group.pid, signal.SIGKILL)],
        )
        self.assertFalse(control.leader_exited())
        final_output = output.read_bytes()
        time.sleep(0.15)
        self.assertEqual(output.read_bytes(), final_output)

    def test_live_leader_is_stopped_before_reaping(self):
        group = self.start("import time; time.sleep(60)")
        self.assertLess(group.stop(), 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(group.pid, os.WNOHANG)

    def test_inspection_failure_retains_pin_and_allows_same_owner_retry(self):
        group = self.start("import time; time.sleep(60)")
        with (
            patch(
                "todo_flow.owned_process_group.subprocess.run",
                side_effect=PermissionError("inspection denied"),
            ),
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            with self.assertRaises(VerificationCleanupError):
                group.stop()
        send.assert_not_called()
        self.assertIsNone(group._proc.returncode)
        self.assertFalse(group.leader_exited())
        self.assertLess(group.stop(), 0)

    def test_signal_failure_is_not_cleanup_confirmation(self):
        group = self.start("import time; time.sleep(60)")
        with patch(
            "todo_flow.owned_process_group.os.killpg",
            side_effect=PermissionError("signal denied"),
        ):
            with self.assertRaises(VerificationCleanupError):
                group.stop()
        self.assertIsNone(group._proc.returncode)
        self.assertFalse(group.leader_exited())
        self.assertLess(group.stop(), 0)

    def test_interrupted_reap_never_signals_numeric_group_on_retry(self):
        group = self.start("pass")
        self.await_exit(group)
        wait = group._proc.wait

        def interrupted_wait(**kwargs):
            wait(**kwargs)
            raise KeyboardInterrupt

        with patch.object(group._proc, "wait", side_effect=interrupted_wait):
            with self.assertRaises(KeyboardInterrupt):
                group.stop()
        with (
            patch("todo_flow.owned_process_group.subprocess.run") as inspect,
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            self.assertEqual(group.stop(), 0)
            self.assertEqual(group.stop(), 0)
            self.assertTrue(group.leader_exited())
        inspect.assert_not_called()
        send.assert_not_called()

    def test_missing_leader_is_unknown_and_never_signalled(self):
        group = self.start("import time; time.sleep(60)")
        snapshot = subprocess.CompletedProcess([], 0, "1 1 S\n", "")
        with (
            patch("todo_flow.owned_process_group.subprocess.run", return_value=snapshot),
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "leader is missing"):
                group.stop()
        send.assert_not_called()
        self.assertIsNone(group._proc.returncode)

    def test_sigchld_auto_reaping_prevents_spawn(self):
        with (
            patch("todo_flow.owned_process_group.signal.getsignal", return_value=signal.SIG_IGN),
            patch("todo_flow.owned_process_group.subprocess.Popen") as spawn,
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "SIGCHLD"):
                OwnedProcessGroup([sys.executable, "-c", "pass"])
        spawn.assert_not_called()

    def test_foreign_driver_cannot_signal_the_group(self):
        group = self.start("import time; time.sleep(60)")
        driver = os.getpid()
        with (
            patch("todo_flow.owned_process_group.os.getpid", return_value=driver + 1),
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "fork"):
                group.stop()
        send.assert_not_called()
