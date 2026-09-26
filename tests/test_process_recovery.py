import tempfile
import unittest
from unittest.mock import patch

from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.process_recovery import require_recovery_clear


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name

    def barrier(self, track="track"):
        return ProcessBarrier(self.directory, track)

    def check(self, attempt="attempt", **kwargs):
        return require_recovery_clear(
            self.directory, "track", attempt, task="task", generation=1, **kwargs
        )

    def cancel(self, attempt, execution):
        gate = LaunchGate.prepare(self.directory, "track", attempt, execution, backend="test")
        gate.cancel_pending()

    def test_missing_journal_creates_attributed_durable_hold(self):
        with self.assertRaises(ProcessBarrierError):
            self.check()
        history = self.barrier().history()
        self.assertEqual([event["state"] for event in history], ["intent", "unknown"])
        self.assertEqual(history[-1]["attempt"], "attempt")
        self.assertEqual(history[-1]["evidence"]["task"], "task")
        self.assertEqual(history[-1]["evidence"]["generation"], 1)
        self.assertIn(str(self.barrier().path), self.assert_blocked())
        self.barrier("other-track").require_clear()

    def assert_blocked(self):
        with self.assertRaises(ProcessBarrierError) as caught:
            self.barrier().require_clear()
        return str(caught.exception)

    def test_repeated_recovery_preserves_the_original_hold(self):
        with self.assertRaises(ProcessBarrierError):
            self.check()
        original = self.barrier().path.read_bytes()
        for _ in range(2):
            with self.assertRaises(ProcessBarrierError):
                self.check()
            self.assertEqual(self.barrier().path.read_bytes(), original)

    def test_another_attempts_confirmation_does_not_cover_expired_attempt(self):
        self.cancel("previous-attempt", "previous-execution")
        previous = self.barrier().history()
        with self.assertRaises(ProcessBarrierError):
            self.check()
        history = self.barrier().history()
        self.assertEqual(history[: len(previous)], previous)
        self.assertEqual(history[-1]["attempt"], "attempt")
        self.assertEqual(history[-1]["state"], "unknown")

    def test_cancelled_launch_does_not_prove_complete_attempt_inventory(self):
        self.cancel("attempt", "execution")
        original = self.barrier().history()
        with self.assertRaisesRegex(ProcessBarrierError, "all process launches"):
            self.check()
        history = self.barrier().history()
        self.assertEqual(history[: len(original)], original)
        self.assertEqual(history[-1]["evidence"]["recorded_executions"], ["execution"])
        self.assertEqual(history[-1]["state"], "unknown")
        held = self.barrier().path.read_bytes()
        with self.assertRaises(ProcessBarrierError):
            self.check()
        self.assertEqual(self.barrier().path.read_bytes(), held)
        self.barrier("other-track").require_clear()

    def test_multiple_confirmed_groups_do_not_prove_inventory_completeness(self):
        for execution in ("worker", "verification"):
            barrier = self.barrier()
            barrier.begin("attempt", execution, reason="launch", evidence={"backend": "test"})
            revision = barrier.advance(
                "attempt",
                execution,
                "cleaning",
                expected_revision=len(barrier.history()),
                reason="cleanup",
                evidence={"fixture": "supervisor"},
            )
            barrier.advance(
                "attempt",
                execution,
                "confirmed",
                expected_revision=revision,
                reason="owned group exited",
                evidence={
                    "identity": {"track": "track", "attempt": "attempt", "execution": execution},
                    "outcome": "group-exited",
                    "proof": "Synthetic supervisor exit fixture",
                },
            )
        original = self.barrier().history()
        with self.assertRaises(ProcessBarrierError):
            self.check()
        history = self.barrier().history()
        self.assertEqual(history[: len(original)], original)
        self.assertEqual(history[-1]["evidence"]["recorded_executions"], ["verification", "worker"])

    def test_later_unresolved_execution_cannot_be_hidden_by_confirmation(self):
        self.cancel("attempt", "execution")
        self.barrier().begin(
            "attempt", "later", reason="second launch", evidence={"backend": "test"}
        )
        original = self.barrier().path.read_bytes()
        with self.assertRaises(ProcessBarrierError):
            self.check()
        self.assertEqual(self.barrier().path.read_bytes(), original)

    def test_corrupt_evidence_is_never_replaced(self):
        self.barrier().path.write_bytes(b"{interrupted")
        with self.assertRaises(ProcessBarrierError):
            self.check()
        self.assertEqual(self.barrier().path.read_bytes(), b"{interrupted")

    def test_interrupted_unknown_write_leaves_a_blocking_intent(self):
        with patch.object(ProcessBarrier, "advance", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.check()
        original = self.barrier().path.read_bytes()
        self.assertEqual(self.barrier().history()[-1]["state"], "intent")
        with self.assertRaises(ProcessBarrierError):
            self.check()
        self.assertEqual(self.barrier().path.read_bytes(), original)

    def test_failed_initial_persistence_cannot_authorize_recovery(self):
        with patch("todo_flow.process_barrier.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(ProcessBarrierError):
                self.check()
        self.assert_blocked()

    def test_missing_attempt_identity_cannot_be_guessed(self):
        for attempt in (None, "", " "):
            with self.subTest(attempt=attempt):
                with self.assertRaises(ProcessBarrierError):
                    self.check(attempt)
        self.assertFalse(self.barrier().path.exists())
