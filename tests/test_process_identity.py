import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from todo_flow import verification


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class ProcessIdentityTests(unittest.TestCase):
    def start(self, code="import time; time.sleep(60)", *, new_session=True):
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=new_session,
        )
        self.addCleanup(self.dispose, proc)
        return proc

    def dispose(self, proc):
        # These fixtures have no descendants. Never signal the test runner's group.
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    def test_reaped_pid_replaced_by_live_control_is_not_signalled(self):
        original = self.start("pass")
        original.wait(timeout=5)
        control = self.start()
        original_pid = original.pid
        # Deterministically reproduce a reaped Popen whose numeric PID now names
        # another real session. Waiting for actual kernel PID reuse is unreliable.
        original.pid = control.pid
        try:
            with patch("todo_flow.verification.os.killpg", wraps=os.killpg) as send:
                with self.assertRaisesRegex(
                    verification.VerificationCleanupError, "reaped PID"
                ):
                    verification.stop_group(original, collect_output=False)
                send.assert_not_called()
            self.assertIsNone(control.poll())
        finally:
            original.pid = original_pid

    def test_child_without_dedicated_session_is_rejected(self):
        proc = self.start(new_session=False)
        with patch("todo_flow.verification.os.killpg") as send:
            with self.assertRaisesRegex(
                verification.VerificationCleanupError, "session/group leader"
            ):
                verification.stop_group(proc, collect_output=False)
            send.assert_not_called()
        self.assertIsNone(proc.poll())

    def test_session_mismatch_does_not_signal_matching_group(self):
        proc = self.start()
        with (
            patch("todo_flow.verification.os.getsid", return_value=proc.pid + 1),
            patch("todo_flow.verification.os.killpg") as send,
        ):
            with self.assertRaisesRegex(
                verification.VerificationCleanupError, "session/group leader"
            ):
                verification.stop_group(proc, collect_output=False)
            send.assert_not_called()
        self.assertIsNone(proc.poll())

    def test_identity_inspection_denial_is_not_exit_evidence(self):
        proc = self.start()
        for operation in ("getpgid", "getsid"):
            with self.subTest(operation=operation):
                with (
                    patch(
                        "todo_flow.verification.os." + operation,
                        side_effect=PermissionError("denied"),
                    ),
                    patch("todo_flow.verification.os.killpg") as send,
                ):
                    with self.assertRaises(verification.VerificationCleanupError) as raised:
                        verification.stop_group(proc, collect_output=False)
                    self.assertIsInstance(raised.exception.__cause__, PermissionError)
                    send.assert_not_called()
                self.assertIsNone(proc.poll())

    def test_identity_is_rechecked_after_group_inspection(self):
        proc = self.start()
        with (
            patch("todo_flow.verification.os.getsid", side_effect=[proc.pid, proc.pid + 1]),
            patch("todo_flow.verification.os.killpg") as send,
        ):
            with self.assertRaisesRegex(
                verification.VerificationCleanupError, "session/group leader"
            ):
                verification.stop_group(proc, collect_output=False)
            send.assert_not_called()
        self.assertIsNone(proc.poll())
