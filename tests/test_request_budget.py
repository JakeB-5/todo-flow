"""Request limits exercise the actual engine and local command-worker boundary."""

import concurrent.futures
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import test_flow
from todo_flow.cli import parser
from todo_flow.engine import Engine
from todo_flow.store import Conflict, Store


class BudgetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.register(test_flow.DOC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_stop_preserves_candidate_and_pending_tasks(self):
        self.store.start("addition", worker_limit=1)
        task = self.store.claim("first")
        with self.store.transaction() as c:
            c.execute("UPDATE tracks SET head='preserved-head' WHERE id='addition'")
        self.store.finish(
            task,
            {"summary": "Preserved result", "next": [{"kind": "work", "purpose": "Remaining"}]},
        )
        before = self.store.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda n: self.store.claim(str(n)), range(8)))
        self.assertTrue(all(claim is None for claim in claims))
        after = self.store.snapshot()
        self.assertEqual(after["results"], before["results"])
        self.assertEqual(after["tasks"], before["tasks"])
        self.assertEqual(after["tracks"][0]["head"], "preserved-head")
        self.assertEqual(after["budgets"][0]["used"], 1)
        self.assertEqual(len(after["decisions"]), 1)
        question = after["decisions"][0]["question"]
        self.assertIn("preserved-head", question)
        self.assertIn("Remaining", question)
        self.assertIn('"used": 1', question)
        events = [e for e in after["events"] if e["type"] == "execution.budget-exhausted"]
        self.assertEqual(len(events), 1)
        details = json.loads(events[0]["body"])
        self.assertEqual(details["candidate_head"], "preserved-head")
        self.assertEqual(details["required_conditions"], test_flow.DOC["conditions"])
        self.assertEqual(details["remaining_tasks"][0]["status"], "queued")
        decision = after["decisions"][0]["id"]
        with self.assertRaises(Conflict):
            self.store.answer(decision, "Continue")
        self.store.control("addition", "pause")
        with self.assertRaises(Conflict):
            self.store.control("addition", "resume")
        self.assertTrue(self.store.start("addition", worker_limit=1)["existing"])
        with self.assertRaises(Conflict):
            self.store.start("addition", worker_limit=3)
        for invalid in (0, -1, True, "2"):
            with self.assertRaises(ValueError):
                self.store.answer(decision, "Approved", invalid)
        extended = self.store.answer(decision, "Approve one more", 1)
        self.assertEqual((extended["worker_limit"], extended["used"]), (2, 1))
        next_task = self.store.claim("next")
        self.assertEqual(next_task["id"], before["tasks"][-1]["id"])
        self.assertEqual(next_task["purpose"], "Remaining")
        with self.assertRaises(Conflict):
            self.store.answer(decision, "Duplicate approval", 1)

    def test_process_pause_does_not_reset_usage(self):
        self.store.start("addition", worker_limit=2)
        task = self.store.claim("first")
        self.store.control("addition", "pause")
        self.store.finish(
            task, {"summary": "First", "next": [{"kind": "work", "purpose": "Second"}]}
        )
        self.store.control("addition", "resume")
        self.assertEqual(self.store.snapshot()["budgets"][0]["used"], 1)
        self.assertIsNotNone(self.store.claim("second"))
        self.assertEqual(self.store.snapshot()["budgets"][0]["used"], 2)

    def test_blocked_track_does_not_starve_other_requests(self):
        self.store.start("addition", "batch", worker_limit=1)
        task = self.store.claim("first")
        self.store.finish(
            task, {"summary": "First", "next": [{"kind": "work", "purpose": "Blocked"}]}
        )
        self.store.register({**test_flow.DOC, "id": "other"})
        self.store.start("other", "batch", worker_limit=1)
        other = self.store.claim("other")
        self.assertEqual(other["track"], "other")
        self.assertEqual(self.store.track("addition")["control"], "paused")
        budgets = self.store.snapshot()["budgets"]
        self.assertEqual(len(budgets), 2)
        self.assertTrue(all(b["used"] == 1 for b in budgets))

    def test_host_tasks_do_not_consume_worker_attempts(self):
        self.store.start("addition", worker_limit=1)
        task = self.store.claim("worker")
        self.store.finish(
            task, {"summary": "Ready", "next": [{"kind": "verify", "purpose": "Verify"}]}
        )
        task = self.store.claim("host")
        self.assertEqual(task["kind"], "verify")
        self.assertEqual(self.store.snapshot()["budgets"][0]["used"], 1)
        self.store.finish(
            task, {"summary": "Verified", "next": [{"kind": "review", "purpose": "Review"}]}
        )
        self.assertIsNone(self.store.claim("reviewer"))
        self.assertEqual(self.store.snapshot()["tasks"][-1]["status"], "queued")

    def test_default_and_validation(self):
        for value in (0, -1, True, "1"):
            with self.assertRaises(ValueError):
                self.store.start("addition", worker_limit=value)
        self.assertEqual(self.store.snapshot()["tasks"], [])
        self.store.start("addition")
        task = self.store.claim("first")
        self.store.finish(task, {"summary": "First", "next": [{"kind": "work", "purpose": "Next"}]})
        self.assertIsNotNone(self.store.claim("second"))
        budget = self.store.snapshot()["budgets"][0]
        self.assertIsNone(budget["worker_limit"])
        self.assertEqual(budget["used"], 2)
        args = parser().parse_args(["trackrun", "addition", "--worker-attempt-limit", "3"])
        self.assertEqual(args.worker_attempt_limit, 3)
        self.assertEqual(args.max_tasks, 100)
        args = parser().parse_args(["start", "addition", "--worker-attempt-limit", "2"])
        self.assertEqual(args.worker_attempt_limit, 2)
        args = parser().parse_args(
            ["answer", "decision-id", "--text", "Approved", "--additional-worker-attempts", "2"]
        )
        self.assertEqual(args.additional_worker_attempts, 2)


class BudgetEngineTests(unittest.TestCase):
    def setUp(self):
        test_flow.IntegrationTests.setUp(self)

    def tearDown(self):
        test_flow.IntegrationTests.tearDown(self)

    def engine(self, mode="repeat"):
        engine = Engine(self.s)
        engine.config["cleanup_on_complete"] = False
        if mode != "normal":
            engine.config["worker"] = {
                "type": "command",
                "argv": [sys.executable, str(Path(__file__).with_name("budget_worker.py")), mode],
            }
        return engine

    def test_repeating_plan_stops_across_driver_and_cache_restarts(self):
        self.s.start("addition", worker_limit=3)
        self.assertEqual(self.engine().run(jobs=2, max_tasks=1), 1)
        self.assertEqual(self.s.track("addition")["control"], "active")
        shutil.rmtree(self.s.path / ".cache")
        self.s = Store(self.s.path)
        self.assertEqual(self.engine().run(jobs=2, max_tasks=20), 2)
        stopped = self.s.snapshot()
        self.assertEqual(stopped["budgets"][0]["used"], 3)
        self.assertEqual(len(stopped["attempts"]), 3)
        self.assertEqual(len(stopped["results"]), 3)
        self.assertEqual(stopped["tracks"][0]["status"], "open")
        self.assertEqual(stopped["tracks"][0]["control"], "paused")
        self.assertEqual(len(stopped["decisions"]), 1)
        self.assertTrue(any(t["status"] == "queued" for t in stopped["tasks"]))
        self.assertEqual(self.engine().run(max_tasks=20), 0)
        self.assertEqual(self.s.snapshot()["attempts"], stopped["attempts"])
        decision = stopped["decisions"][0]["id"]
        with self.assertRaises(Conflict):
            self.s.answer(decision, "Continue")
        approved = self.s.answer(decision, "Approve two additional attempts", 2)
        self.assertEqual((approved["worker_limit"], approved["used"]), (5, 3))
        self.s = Store(self.s.path)
        self.assertEqual(self.engine().run(max_tasks=20), 2)
        final = self.s.snapshot()
        self.assertEqual(final["budgets"][0]["used"], 5)
        self.assertEqual(len(final["attempts"]), 5)
        self.assertEqual(len([d for d in final["decisions"] if d["status"] == "open"]), 1)
        extension = next(e for e in final["events"] if e["type"] == "execution.budget-extended")
        details = json.loads(extension["body"])
        self.assertEqual(details["previous_limit"], 3)
        self.assertEqual(details["additional_worker_attempts"], 2)
        self.assertEqual(details["used"], 3)
        self.assertTrue(details["candidate_head"])
        self.assertTrue(details["remaining_tasks"])

    def test_failure_consumes_attempt_and_answer_cannot_reset_budget(self):
        self.s.start("addition", worker_limit=1)
        self.assertEqual(self.engine("fail").run(max_tasks=20), 1)
        failed = self.s.snapshot()
        self.assertEqual(failed["budgets"][0]["used"], 1)
        self.assertTrue(any(e["type"] == "attempt.error" for e in failed["events"]))
        recovery = next(d for d in failed["decisions"] if d["status"] == "open")
        self.s.answer(recovery["id"], "Use the working adapter")
        self.s = Store(self.s.path)
        self.assertEqual(self.engine().run(max_tasks=20), 0)
        stopped = self.s.snapshot()
        self.assertEqual(stopped["budgets"][0]["used"], 1)
        self.assertEqual(len(stopped["attempts"]), 1)
        self.assertEqual(stopped["results"], failed["results"])
        budget_decision = next(d for d in stopped["decisions"] if d["status"] == "open")
        self.s.answer(budget_decision["id"], "Approve one retry", 1)
        self.assertEqual(self.engine().run(max_tasks=20), 1)
        self.assertEqual(self.s.snapshot()["budgets"][0]["used"], 2)

    def test_ordinary_worker_question_keeps_cumulative_usage(self):
        self.s.start("addition", worker_limit=2)
        self.assertEqual(self.engine("question").run(max_tasks=20), 1)
        decision = self.s.snapshot()["decisions"][0]["id"]
        with self.assertRaises(Conflict):
            self.s.answer(decision, "Implement it", 2)
        self.s.answer(decision, "Implement it")
        self.s = Store(self.s.path)
        self.assertEqual(self.engine().run(max_tasks=20), 1)
        stopped = self.s.snapshot()
        self.assertEqual(stopped["budgets"][0]["used"], 2)
        self.assertEqual(len(stopped["attempts"]), 2)
        self.assertEqual(stopped["tracks"][0]["status"], "open")
        self.assertEqual(stopped["tracks"][0]["control"], "paused")

    def test_normal_work_keeps_verification_independent_review_and_triage(self):
        self.s.start("addition", worker_limit=4)
        self.assertEqual(self.engine("normal").run(jobs=2, max_tasks=20), 6)
        snap = self.s.snapshot()
        track = snap["tracks"][0]
        self.assertEqual(track["status"], "done", json.dumps(snap["decisions"]))
        self.assertEqual(snap["budgets"][0]["used"], 4)
        self.assertEqual(snap["decisions"], [])
        verification = json.loads(track["verification"])
        self.assertTrue(verification["ok"])
        review_task = next(t for t in snap["tasks"] if t["kind"] == "review")
        work_task = next(t for t in snap["tasks"] if t["kind"] == "work")
        review_attempt = next(a for a in snap["attempts"] if a["task"] == review_task["id"])
        work_attempt = next(a for a in snap["attempts"] if a["task"] == work_task["id"])
        self.assertNotEqual(review_attempt["id"], work_attempt["id"])
        self.assertTrue(track["review"])
        self.assertTrue(track["landing"])
        self.assertTrue(snap["triages"])
        self.assertEqual(self.engine("normal").run(max_tasks=20), 0)
