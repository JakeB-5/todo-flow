"""Exercise expired-claim safety through the production Engine entry point."""

import json
import os
import unittest
from unittest.mock import patch

import test_claim_recovery
import test_flow
from todo_flow.adapters import file_lock
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Store


class EngineClaimRecoveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    expire = test_claim_recovery.ExpiredClaimRecoveryTests.expire
    cancel = test_claim_recovery.ExpiredClaimRecoveryTests.cancel

    def assert_preserved(self, before):
        after = self.s.snapshot()
        for key in ("tasks", "attempts"):
            self.assertEqual(after[key], before[key], key)
        self.assertIsNone(self.s.claim("replacement"))
        self.assertFalse(any(e["type"] == "claim.recovered" for e in after["events"]))

    def diagnostics(self):
        return [
            json.loads(event["body"])
            for event in self.s.snapshot()["events"]
            if event["type"] == "claim.recovery_blocked"
        ]

    def test_no_journal_blocks_reopened_engine_and_preserves_reason(self):
        self.expire()
        before = self.s.snapshot()
        self.engine.reconcile()
        self.assert_preserved(before)
        self.assertEqual(self.engine.process_barrier("addition").history()[-1]["state"], "unknown")
        first = self.diagnostics()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["workId"], self.task["id"])
        self.assertIn("Reconcile", first[0]["retry"])
        reopened = Engine(Store(self.s.path))
        reopened.reconcile()
        self.assert_preserved(before)
        self.assertEqual(self.diagnostics(), first)
        with self.assertRaises(ProcessBarrierError):
            reopened.verify(self.task, self.repo)
        with self.assertRaises(ProcessBarrierError):
            reopened.apply_changes(
                self.task, self.repo, [{"path": "calc.py", "content": "unexpected"}]
            )
        self.assertIn("NotImplementedError", (self.repo / "calc.py").read_text())

    def test_other_attempt_evidence_cannot_requeue_current_claim(self):
        self.expire()
        self.cancel("other-attempt")
        before = self.s.snapshot()
        self.engine.reconcile()
        self.assert_preserved(before)
        self.assertEqual(self.engine.process_barrier("addition").history()[-1]["state"], "unknown")

    def test_lock_contention_preserves_claim_and_other_track_recovers(self):
        self.expire()
        self.cancel()
        self.s.register({**test_flow.DOC, "id": "second"})
        self.s.start("second")
        second = self.s.claim("other-driver")
        LaunchGate.prepare(
            self.s.path, "second", second["attempt"], "never-dispatched", backend="test"
        ).cancel_pending()
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (second["id"],))
        before = next(row for row in self.s.snapshot()["tasks"] if row["id"] == self.task["id"])
        with file_lock(self.lock):
            self.engine.reconcile()
            first = self.diagnostics()
            Engine(Store(self.s.path)).reconcile()
            self.assertEqual(self.diagnostics(), first)
        snapshot = self.s.snapshot()
        self.assertEqual(
            next(row for row in snapshot["tasks"] if row["id"] == self.task["id"]), before
        )
        other = next(row for row in snapshot["tasks"] if row["id"] == second["id"])
        self.assertEqual(other["status"], "queued")
        self.assertGreater(other["generation"], second["generation"])
        self.assertIn("owned", self.diagnostics()[0]["reason"])
        Engine(Store(self.s.path)).reconcile()
        current = next(row for row in self.s.snapshot()["tasks"] if row["id"] == self.task["id"])
        self.assertEqual(current["status"], "queued")
        self.assertGreater(current["generation"], self.task["generation"])

    def test_missing_and_duplicate_attempts_remain_blocked_and_diagnosable(self):
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
                Engine(Store(self.s.path)).reconcile()
                self.assert_preserved(before)
                first = self.diagnostics()
                Engine(Store(self.s.path)).reconcile()
                self.assert_preserved(before)
                self.assertEqual(self.diagnostics(), first)
                messages = [row["reason"] for row in self.diagnostics()]
                self.assertTrue(any(f"has {count} running attempts" in row for row in messages))
                self.assertTrue(all("reconcile attempt identity" in row for row in messages))

    def test_journal_write_failure_preserves_claim_in_engine_recovery(self):
        self.expire()
        before = self.s.snapshot()
        fsync = os.fsync
        failed = False

        def fail_first_sync(fd):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("disk failure")
            return fsync(fd)

        with patch("todo_flow.process_barrier.os.fsync", side_effect=fail_first_sync):
            self.engine.reconcile()
        self.assertTrue(failed)
        self.assert_preserved(before)
        self.assertTrue(self.diagnostics())
        with self.assertRaises(ProcessBarrierError):
            Engine(Store(self.s.path)).process_barrier("addition").require_clear()
