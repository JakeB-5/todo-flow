import sys
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.store import Store
from todo_flow.verification import VerificationCleanupError


class EngineProcessBarrierTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def claim(self):
        self.s.start("addition")
        return self.s.claim("driver")

    def reopen(self):
        return Engine(Store(self.s.path))

    def test_restart_blocks_before_workspace_at_each_unresolved_stage(self):
        task = self.claim()
        barrier = self.reopen().process_barrier("addition")
        revision = barrier.begin(
            task["attempt"], "execution", reason="launch", evidence={"backend": "test"}
        )
        for state in ("intent", "running", "cleaning", "unknown", "cleaning"):
            if state != "intent":
                revision = barrier.advance(
                    task["attempt"],
                    "execution",
                    state,
                    expected_revision=revision,
                    reason="interrupted supervisor",
                    evidence={"stage": state},
                )
            with self.subTest(state=state):
                engine = self.reopen()
                with (
                    patch.object(engine, "ensure_workspace") as workspace,
                    patch.object(engine, "fail") as failed,
                    patch("todo_flow.engine.run_worker") as worker,
                ):
                    engine.execute(task)
                workspace.assert_not_called()
                worker.assert_not_called()
                self.assertIsInstance(failed.call_args.args[1], ProcessBarrierError)
                with self.assertRaises(ProcessBarrierError):
                    engine.verify(task, self.repo)
                self.assertEqual(barrier.history()[-1]["state"], state)

    def test_cleanup_failure_survives_answer_and_new_engine(self):
        task = self.claim()
        engine = self.reopen()
        engine.fail(task, VerificationCleanupError("Cannot confirm exit"))
        barrier = engine.process_barrier("addition")
        history = barrier.history()
        self.assertEqual(history[-1]["state"], "unknown")
        self.assertEqual(history[-1]["attempt"], task["attempt"])
        decision = self.s.snapshot()["decisions"][0]
        self.s.answer(decision["id"], "Retry now")
        resumed = self.s.claim("successor")
        self.assertIsNotNone(resumed)
        engine = self.reopen()
        with patch.object(engine, "ensure_workspace") as workspace:
            engine.execute(resumed)
        workspace.assert_not_called()
        self.assertEqual(barrier.history(), history)
        with self.assertRaises(ProcessBarrierError):
            engine.process_barrier("addition").require_clear()
        self.assertFalse(any(t["status"] == "queued" for t in self.s.snapshot()["tasks"]))

    def test_expired_claim_is_not_requeued_when_cleanup_is_unresolved(self):
        task = self.claim()
        barrier = self.reopen().process_barrier("addition")
        barrier.begin(task["attempt"], "execution", reason="launch", evidence={"stage": "intent"})
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        before = self.s.snapshot()
        self.reopen().reconcile()
        after = self.s.snapshot()
        self.assertEqual(after["tasks"], before["tasks"])
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertIsNone(self.s.claim("replacement"))
        self.assertFalse(any(e["type"] == "claim.recovered" for e in after["events"]))

    def test_successful_verification_cache_cannot_bypass_new_hold(self):
        task = self.claim()
        engine = self.reopen()
        workspace = engine.ensure_workspace(task)
        engine.config["verify"] = [sys.executable, "-c", "pass"]
        self.assertTrue(engine.verify(task, workspace)["ok"])
        engine.process_barrier("addition").begin(
            task["attempt"], "late-launch", reason="launch", evidence={"stage": "intent"}
        )
        recovered = self.reopen()
        recovered.config["verify"] = engine.config["verify"]
        with patch("todo_flow.engine.run_verification") as run:
            with self.assertRaises(ProcessBarrierError):
                recovered.verify(task, workspace)
        run.assert_not_called()
        with self.assertRaises(ProcessBarrierError):
            recovered.apply_changes(task, workspace, [{"path": "calc.py", "content": "changed"}])
        self.assertIn("NotImplementedError", (workspace / "calc.py").read_text())

    def test_corrupt_evidence_is_not_replaced_by_failure_or_recovery(self):
        task = self.claim()
        engine = self.reopen()
        barrier = engine.process_barrier("addition")
        barrier.path.write_bytes(b"{interrupted")
        engine.fail(task, VerificationCleanupError("inspection failed"))
        self.reopen().reconcile()
        self.assertEqual(barrier.path.read_bytes(), b"{interrupted")
        self.assertFalse(any(t["status"] == "queued" for t in self.s.snapshot()["tasks"]))
        with self.assertRaises(ProcessBarrierError):
            self.reopen().process_barrier("addition").require_clear()

    def test_hold_for_one_track_does_not_block_another(self):
        task = self.claim()
        self.reopen().preserve_cleanup_failure(task, VerificationCleanupError("unknown"))
        self.reopen().process_barrier("other").require_clear()
