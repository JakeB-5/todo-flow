import concurrent.futures
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow.adapters import command, permitted
from todo_flow.engine import Engine
from todo_flow.store import Conflict, Store, encode

DOC = {
    "id": "addition",
    "title": "Add numbers",
    "goal": "Implement addition",
    "scope": "calc.py plus tests",
    "evidence": "A user needs integer addition",
    "conditions": [{"id": "sum", "text": "2 + 3 = 5", "method": "unittest"}],
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / "state")
        self.s.register(DOC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_registration_idempotence_and_revision(self):
        self.assertTrue(self.s.register(DOC)["existing"])
        modified = {**DOC, "goal": "New goal"}
        with self.assertRaises(Conflict):
            self.s.register(modified)
        self.assertEqual(self.s.register(modified, 1)["revision"], 2)

    def test_concurrent_claim_only_one_winner(self):
        self.s.start("addition", "same")
        self.assertTrue(self.s.start("addition", "same")["existing"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.s.claim(str(n)), range(8)))
        self.assertEqual(sum(x is not None for x in results), 1)

    def test_multiple_tracks_same_request_id_get_own_tasks(self):
        self.s.register({**DOC, "id": "other"})
        self.s.start("addition", "batch")
        self.s.start("other", "batch")
        self.assertEqual(len(self.s.snapshot()["tasks"]), 2)

    def test_stale_result_cannot_write(self):
        self.s.start("addition")
        task = self.s.claim("old")
        self.s.control("addition", "cancel")
        with self.assertRaises(Conflict):
            self.s.finish(task, {"summary": "late result"})
        self.assertEqual(self.s.snapshot()["results"], [])

    def test_result_and_followup_atomic(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        with self.assertRaises(ValueError):
            self.s.finish(task, {"summary": "x", "next": [{"kind": "bad", "purpose": "x"}]})
        self.assertEqual(self.s.snapshot()["results"], [])
        self.assertEqual(self.s.snapshot()["tasks"][0]["status"], "running")

    def test_decision_resume_without_parent(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        self.s.finish(task, {"summary": "Need input", "question": "Which type?"})
        d = self.s.snapshot()["decisions"][0]
        other = Store(self.s.path)
        other.answer(d["id"], "Integers")
        next_task = other.claim("successor")
        self.assertIn("Integers", next_task["purpose"])
        with self.assertRaises(Conflict):
            other.answer(d["id"], "Another answer")

    def test_pause_records_result_but_does_not_start_followup(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        self.s.control("addition", "pause")
        self.s.finish(task, {"summary": "preserved", "next": [{"kind": "work", "purpose": "next"}]})
        self.assertIsNone(self.s.claim("new"))
        self.s.control("addition", "resume")
        self.assertEqual(self.s.claim("new")["kind"], "work")

    def test_path_boundary(self):
        for path in ("../x.py", "/tmp/x.py", ".git/hooks/x.py", "foo/.env"):
            self.assertFalse(permitted(path, ["*"]))
        self.assertTrue(permitted("src/calc.py", ["src/*.py"]))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        command(["git", "init", "--bare", str(self.remote)])
        self.repo = self.root / "repo"
        command(["git", "init", "-b", "main", str(self.repo)])
        command(["git", "config", "user.email", "test@example.invalid"], self.repo)
        command(["git", "config", "user.name", "Flow Test"], self.repo)
        (self.repo / ".gitignore").write_text("__pycache__/\n.todo-flow/\n")
        (self.repo / "calc.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Initial test fixture"], self.repo)
        command(["git", "remote", "add", "origin", str(self.remote)], self.repo)
        command(["git", "push", "-u", "origin", "main"], self.repo)
        self.s = Store(self.root / "state")
        self.s.configure(
            {
                "repo": str(self.repo),
                "github": None,
                "base": "main",
                "verify": [sys.executable, "-m", "unittest", "discover", "-v"],
                "worker": {
                    "type": "command",
                    "argv": [sys.executable, str(Path(__file__).with_name("fake_worker.py"))],
                },
                "writable_patterns": ["*.py"],
                "context_patterns": ["*.py"],
                "endpoint": "land",
                "allow_land": True,
            }
        )
        self.s.register(DOC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_git_lifecycle_and_idempotent_restart(self):
        self.s.start("addition")
        count = Engine(self.s).run(jobs=2, max_tasks=20)
        snap = self.s.snapshot()
        self.assertEqual(snap["tracks"][0]["status"], "done", encode(snap["decisions"]))
        self.assertEqual(count, 6)
        remote_code = command(["git", "--git-dir", str(self.remote), "show", "main:calc.py"])
        self.assertIn("return a + b", remote_code)
        self.assertEqual(Engine(self.s).run(max_tasks=3), 0)
        self.assertTrue(all(w["status"] == "done" for w in snap["tasks"]))

    def test_recovery_fences_expired_claim(self):
        self.s.start("addition")
        old = self.s.claim("dead")
        with self.s.transaction() as c:
            c.execute("UPDATE tasks SET lease=?", (time.time() - 60,))
        Engine(self.s).reconcile()
        new = self.s.claim("new")
        self.assertGreater(new["generation"], old["generation"])
        with self.assertRaises(Conflict):
            self.s.finish(old, {"summary": "late"})
        self.s.finish(new, {"summary": "recovered", "question": "continue?"})

    def test_unreviewed_landing_refused(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        with self.assertRaises(Conflict):
            Engine(self.s).land(task)

    def test_response_loss_after_remote_push_reconciles(self):
        self.s.start("addition")
        Engine(self.s).run(max_tasks=3)
        t = self.s.track("addition")
        self.assertIsNotNone(t["review"])
        e = Engine(self.s)
        task = self.s.claim("landing")
        original = e.update

        def fail_receipt(task_, **values):
            if "landing" in values:
                raise RuntimeError("Crash after push")
            return original(task_, **values)

        with patch.object(e, "update", fail_receipt):
            with self.assertRaises(RuntimeError):
                e.land(task)
        result = e.land(task)
        self.assertIn("recovered", result["summary"])
        self.assertTrue(json.loads(self.s.track("addition")["landing"])["recovered"])

    def test_combined_check_prevents_bad_base(self):
        self.s.start("addition")
        Engine(self.s).run(max_tasks=3)
        (self.repo / "test_badbase.py").write_text(
            'import unittest\nclass T(unittest.TestCase):\n def test_fail(self): self.fail("base broken")\n'
        )
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Broken upstream"], self.repo)
        command(["git", "push"], self.repo)
        before = command(["git", "rev-parse", "HEAD"], self.repo)
        task = self.s.claim("landing")
        result = Engine(self.s).land(task)
        self.assertIn("failed", result["summary"])
        self.assertEqual(
            command(["git", "--git-dir", str(self.remote), "rev-parse", "main"]), before
        )

    def test_two_tracks_share_git_metadata_but_not_workspaces(self):
        self.s.register({**DOC, "id": "second"})
        self.s.start("addition")
        self.s.start("second")
        Engine(self.s).run(jobs=2, max_tasks=20)
        snap = self.s.snapshot()
        self.assertTrue(
            all(t["status"] == "done" for t in snap["tracks"]), encode(snap["decisions"])
        )
        self.assertEqual(len({t["workspace"] for t in snap["tracks"]}), 2)
        for t in snap["tracks"]:
            self.assertIn("Ran 1 test", json.loads(t["verification"])["output"])

    def test_review_keeps_extra_observations_without_losing_required_conditions(self):
        self.s.start("addition")
        initial = self.s.claim("initial")
        self.s.finish(
            initial, {"summary": "ready", "next": [{"kind": "review", "purpose": "inspect"}]}
        )
        task = self.s.claim("reviewer")
        e = Engine(self.s)
        e.ensure_workspace(task)
        result = {
            "summary": "met plus scope observation",
            "verdict": "met",
            "conditions": [
                {"id": "sum", "verdict": "met", "evidence": "source"},
                {"id": "scope", "verdict": "met", "evidence": "diff"},
            ],
        }
        e.record_review(task, result)
        review = json.loads(self.s.track("addition")["review"])
        self.assertEqual([c["id"] for c in review["conditions"]], ["sum"])
        self.assertEqual(review["additional_assessments"][0]["id"], "scope")
        with self.assertRaises(ValueError):
            e.record_review(task, {**result, "conditions": result["conditions"][1:]})


if __name__ == "__main__":
    unittest.main()


class FollowupRegressionTests(unittest.TestCase):
    setUp = StoreTests.setUp
    tearDown = StoreTests.tearDown

    def test_same_role_different_wording_joins_existing_request(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        self.s.finish(
            task,
            {
                "summary": "delegate",
                "next": [
                    {"kind": "review", "purpose": "Review code"},
                    {"kind": "review", "purpose": "Independently check all acceptance conditions"},
                ],
            },
        )
        pending = [w for w in self.s.snapshot()["tasks"] if w["kind"] == "review"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            len([e for e in self.s.snapshot()["events"] if e["type"] == "work.joined"]), 1
        )

    def test_dependency_wait_is_not_a_user_question(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        with self.s.transaction() as c:
            dep = self.s.enqueue(c, "addition", "work", "other obligation", "unique")
        self.s.finish(task, {"summary": "Wait for existing work", "wait_for": [dep]})
        self.assertEqual(self.s.snapshot()["decisions"], [])
        self.assertEqual(
            next(w for w in self.s.snapshot()["tasks"] if w["id"] == task["id"])["status"],
            "waiting",
        )

    def test_invalid_followup_rolls_back_result_and_dependency(self):
        self.s.start("addition")
        task = self.s.claim("worker")
        with self.assertRaises(ValueError):
            self.s.finish(task, {"summary": "Bad dependency", "wait_for": [task["id"]]})
        self.assertEqual(self.s.snapshot()["results"], [])
