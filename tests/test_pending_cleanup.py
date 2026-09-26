import gc
import os
import subprocess
import sys
import time
import unittest
import weakref
from unittest.mock import patch

from todo_flow.owned_process_group import OwnedProcessGroup, retry_pending_cleanup
from todo_flow.verification import VerificationCleanupError


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class PendingCleanupTests(unittest.TestCase):
    def start(self, code="import time; time.sleep(60)"):
        owner = OwnedProcessGroup(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.dispose, weakref.ref(owner), owner._proc)
        return owner

    def dispose(self, reference, proc):
        owner = reference()
        if owner is not None:
            owner.stop()
        else:
            # Fixtures have no descendants. Keep the direct child handle solely
            # to prevent a regression in retention from leaking the test process.
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    def fail_cleanup(self, owner):
        with patch(
            "todo_flow.owned_process_group.os.killpg",
            side_effect=PermissionError("injected denial"),
        ):
            with self.assertRaises(VerificationCleanupError):
                owner.stop()

    def test_lost_caller_reference_survives_gc_until_successful_retry(self):
        owner = self.start()
        reference = weakref.ref(owner)
        self.fail_cleanup(owner)
        del owner
        gc.collect()
        self.assertIsNotNone(reference())
        self.assertFalse(reference().leader_exited())
        retry_pending_cleanup()
        gc.collect()
        self.assertIsNone(reference())
        with patch("todo_flow.owned_process_group.os.killpg") as send:
            retry_pending_cleanup()
        send.assert_not_called()

    def test_retry_failure_retains_owner_and_does_not_stop_active_execution(self):
        owner = self.start()
        control = self.start()
        reference = weakref.ref(owner)
        self.fail_cleanup(owner)
        del owner
        gc.collect()
        with patch(
            "todo_flow.owned_process_group.os.killpg",
            side_effect=PermissionError("still denied"),
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "Pending group cleanup failed"):
                retry_pending_cleanup()
        self.assertIsNotNone(reference())
        self.assertFalse(reference().leader_exited())
        self.assertFalse(control.leader_exited())
        retry_pending_cleanup()
        self.assertFalse(control.leader_exited())

    def test_interrupted_reap_retry_never_looks_up_or_signals_released_group(self):
        owner = self.start("pass")
        deadline = time.monotonic() + 5
        while not owner.leader_exited():
            if time.monotonic() >= deadline:
                self.fail("Leader did not exit")
            time.sleep(0.01)
        wait = owner._proc.wait

        def interrupt_after_reap(**kwargs):
            wait(**kwargs)
            raise KeyboardInterrupt

        with patch.object(owner._proc, "wait", side_effect=interrupt_after_reap):
            with self.assertRaises(KeyboardInterrupt):
                owner.stop()
        with (
            patch("todo_flow.owned_process_group.subprocess.run") as inspect,
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            retry_pending_cleanup()
            self.assertEqual(owner.stop(), 0)
        inspect.assert_not_called()
        send.assert_not_called()

    def test_foreign_driver_cannot_retry_inherited_registry(self):
        owner = self.start()
        self.fail_cleanup(owner)
        driver = os.getpid()
        with (
            patch("todo_flow.owned_process_group.os.getpid", return_value=driver + 1),
            patch("todo_flow.owned_process_group.os.killpg") as send,
        ):
            with self.assertRaisesRegex(VerificationCleanupError, "fork"):
                retry_pending_cleanup()
        send.assert_not_called()
        self.assertFalse(owner.leader_exited())
        retry_pending_cleanup()
