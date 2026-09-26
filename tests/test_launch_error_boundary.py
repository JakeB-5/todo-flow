"""Keep backend failures distinct from failures of durable launch fencing."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate


class LaunchErrorBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.gate = LaunchGate.prepare(
            temporary.name, "track", "attempt", "execution", backend="synthetic"
        )

    def test_callback_error_preserves_identity_and_releases_lock(self):
        failure = OSError("Synthetic backend response loss")
        with self.assertRaises(OSError) as raised:
            with self.gate._locked():
                raise failure
        self.assertIs(raised.exception, failure)
        self.gate.cancel_pending()
        self.gate.barrier.require_clear()

    def test_spawn_oserror_preserves_error_and_keeps_launch_uncertain(self):
        failure = OSError("Synthetic spawn failure")
        with self.assertRaises(OSError) as raised:
            with self.gate.launching():
                raise failure
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.gate._event()["state"], "unknown")
        with self.assertRaises(ProcessBarrierError):
            self.gate.cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            self.gate.barrier.require_clear()

    def test_lock_failure_never_enters_callback_and_can_be_retried(self):
        failure = OSError("Synthetic lock failure")
        with patch("todo_flow.process_launch.fcntl.flock", side_effect=failure):
            with self.assertRaises(ProcessBarrierError) as raised:
                with self.gate._locked():
                    self.fail("Callback entered without a lock")
        self.assertIs(raised.exception.__cause__, failure)
        self.gate.cancel_pending()
        self.gate.barrier.require_clear()

    def test_write_error_remains_a_permit_error(self):
        failure = OSError("Synthetic permit write failure")
        stream = Mock()
        stream.write.side_effect = failure
        with self.assertRaises(ProcessBarrierError) as raised:
            self.gate._mark(stream, b"launch\n")
        self.assertIs(raised.exception.__cause__, failure)

    def test_sync_failure_prevents_spawn_and_does_not_refund_permit(self):
        failure = OSError("Synthetic permit sync failure")
        with patch("todo_flow.process_launch.os.fsync", side_effect=failure):
            with self.assertRaises(ProcessBarrierError) as raised:
                with self.gate.launching():
                    self.fail("Spawn preceded durable consumption")
        self.assertIs(raised.exception.__cause__, failure)
        with self.assertRaises(ProcessBarrierError):
            self.gate.cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            self.gate.barrier.require_clear()
