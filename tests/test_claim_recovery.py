from contextlib import contextmanager
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.adapters import file_lock
from todo_flow.claim_recovery import recover_expired_claim
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Conflict, Store


class ExpiredClaimRecoveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def expire(self):
        self.s.start("addition")
        self.task = self.s.claim("dead-driver")
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (self.task["id"],))
            self.expected = dict(
                connection.execute("SELECT * FROM tasks WHERE id=?", (self.task["id"],)).fetchone()
            )
        self.engine = Engine(Store(self.s.path))
        self.lock = self.s.path / "locks" / "addition.lock"

    def cancel(self, attempt=None):
        LaunchGate.prepare(
            self.s.path,
            "addition",
            attempt or self.task["attempt"],
            "execution",
            backend="test",
        ).cancel_pending()

    def recover(self):
        return recover_expired_claim(self.engine.store, self.expected)

    def assert_unchanged(self, before):
        after = self.s.snapshot()
        for key in ("tasks", "attempts", "events"):
            self.assertEqual(after[key], before[key], key)
        self.assertIsNone(self.s.claim("replacement"))

    def test_missing_evidence_blocks_reopened_engine_effects(self):
        self.expire()
        before = self.s.snapshot()
        with self.assertRaises(ProcessBarrierError):
            self.recover()
        self.assert_unchanged(before)
        barrier = self.engine.process_barrier("addition")
        self.assertEqual(barrier.history()[-1]["attempt"], self.task["attempt"])
        self.assertEqual(barrier.history()[-1]["state"], "unknown")
        reopened = Engine(Store(self.s.path))
        with self.assertRaises(ProcessBarrierError):
            reopened.verify(self.task, self.repo)
        with self.assertRaises(ProcessBarrierError):
            reopened.apply_changes(
                self.task, self.repo, [{"path": "calc.py", "content": "unexpected"}]
            )
        self.assertIn("NotImplementedError", (self.repo / "calc.py").read_text())

    def test_other_attempt_confirmation_cannot_recover_this_claim(self):
        self.expire()
        self.cancel("other-attempt")
        before = self.s.snapshot()
        with self.assertRaises(ProcessBarrierError):
            self.recover()
        self.assert_unchanged(before)

    def test_cancelled_launch_recovers_and_fences_old_generation(self):
        self.expire()
        self.cancel()
        self.assertTrue(self.recover())
        replacement = self.s.claim("replacement")
        self.assertGreater(replacement["generation"], self.task["generation"])
        with self.assertRaises(Conflict):
            self.s.finish(self.task, {"summary": "late"})
        attempts = self.s.snapshot()["attempts"]
        previous = next(row for row in attempts if row["id"] == self.task["attempt"])
        self.assertEqual(previous["status"], "abandoned")

    def test_live_execution_lock_prevents_recovery(self):
        self.expire()
        self.cancel()
        before = self.s.snapshot()
        with file_lock(self.lock):
            with self.assertRaises(Conflict):
                self.recover()
        self.assert_unchanged(before)
        self.assertTrue(self.recover())

    def test_execution_lock_remains_held_after_durable_commit(self):
        self.expire()
        self.cancel()
        transaction = self.engine.store.transaction
        observed = []

        @contextmanager
        def observe_commit():
            with transaction() as connection:
                yield connection
            # The store has committed, but the execution lock must still exist.
            with self.assertRaises(Conflict):
                with file_lock(self.lock):
                    self.fail("Execution lock released before durable commit")
            observed.append(True)

        with patch.object(self.engine.store, "transaction", observe_commit):
            self.assertTrue(self.recover())
        self.assertEqual(observed, [True])
        with file_lock(self.lock):
            pass

    def test_changed_claim_is_not_recovered_from_stale_snapshot(self):
        self.expire()
        self.cancel()
        with self.s.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET generation=generation+1,owner='successor' WHERE id=?",
                (self.task["id"],),
            )
        before = self.s.snapshot()
        self.assertFalse(self.recover())
        self.assert_unchanged(before)

    def test_renewed_lease_is_not_recovered(self):
        self.expire()
        self.cancel()
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=9999999999 WHERE id=?", (self.task["id"],))
        before = self.s.snapshot()
        self.assertFalse(self.recover())
        self.assert_unchanged(before)

    def test_missing_and_duplicate_current_attempts_are_not_guessed(self):
        self.expire()
        self.cancel()
        for count in (0, 2):
            with self.subTest(count=count):
                with self.s.transaction() as connection:
                    connection.execute("DELETE FROM attempts WHERE task=?", (self.task["id"],))
                    for index in range(count):
                        connection.execute(
                            "INSERT INTO attempts(id,task,generation,status,started) "
                            "VALUES(?,?,?,'running',0)",
                            (f"ambiguous-{index}", self.task["id"], self.task["generation"]),
                        )
                before = self.s.snapshot()
                with self.assertRaisesRegex(ProcessBarrierError, "reconcile attempt identity"):
                    self.recover()
                self.assert_unchanged(before)

    def test_failed_journal_persistence_cannot_commit_recovery(self):
        self.expire()
        before = self.s.snapshot()
        with patch("todo_flow.process_barrier.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(ProcessBarrierError):
                self.recover()
        self.assert_unchanged(before)
        reopened = Engine(Store(self.s.path))
        with self.assertRaises(ProcessBarrierError):
            reopened.process_barrier("addition").require_clear()
