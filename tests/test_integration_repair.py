import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_flow
from todo_flow import integration
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.store import Conflict

RESOLVED = (
    "def add(a, b):\n"
    "    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):\n"
    "        raise TypeError('Numeric operands required')\n"
    "    return a + b\n"
)


class IntegrationRepairTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def ready(self, conflict=True):
        self.s.start("addition")
        self.e = Engine(self.s)
        self.e.run(max_tasks=3)
        self.before = self.s.track("addition")
        if conflict:
            (self.repo / "calc.py").write_text("def add(a, b):\n    return float(a) + float(b)\n")
        else:
            (self.repo / "test_upstream.py").write_text(
                "import unittest\nfrom calc import add\nclass Upstream(unittest.TestCase):\n"
                "    def test_numeric_only(self):\n"
                "        with self.assertRaises(TypeError):\n            add('2', '3')\n"
            )
        (self.repo / "upstream.txt").write_text("Upstream addition outside the write surface\n")
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Advance base"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        self.base = command(["git", "rev-parse", "HEAD"], self.repo)
        land = self.s.claim("lander")
        self.assertEqual(land["kind"], "land")
        self.e.execute(land)
        return land

    def repair_task(self):
        task = self.s.claim("repairer")
        self.assertEqual(task["kind"], "work")
        return task, Path(self.s.track("addition")["workspace"])

    def assert_delivered(self):
        track = self.s.track("addition")
        self.assertEqual(track["status"], "done", self.s.snapshot()["decisions"])
        self.assertNotEqual(track["head"], self.before["head"])
        self.assertNotEqual(
            json.loads(track["review"])["attempt"], json.loads(self.before["review"])["attempt"]
        )
        self.assertEqual(json.loads(track["review"])["head"], track["head"])
        self.assertEqual(json.loads(track["verification"])["head"], track["head"])
        parents = command(["git", "show", "-s", "--format=%P", track["head"]], self.repo)
        self.assertEqual(parents.split(), [self.before["head"], self.base])
        self.assertEqual(
            command(["git", "show", "origin/main:calc.py"], self.repo), RESOLVED.strip()
        )
        self.assertIn(
            "Upstream addition", command(["git", "show", "origin/main:upstream.txt"], self.repo)
        )
        self.assertFalse(any(e["type"] == "attempt.error" for e in self.s.snapshot()["events"]))

    def test_real_conflict_worker_reads_both_sides_and_new_head_is_reviewed_before_landing(self):
        self.ready()
        track = self.s.track("addition")
        self.assertIsNone(track["review"])
        self.assertIsNone(track["verification"])
        task, workspace = self.repair_task()
        with self.assertRaisesRegex(Conflict, "Integration repair"):
            self.e.gate(task)
        adapter = self.root / "repair_worker.py"
        adapter.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "ctx=json.load(sys.stdin)\n"
            "assert 'files' not in ctx\n"
            "assert Path.cwd()==Path(ctx['workspace'])\n"
            "repair=json.loads(Path(ctx['paths']['integration_repair']).read_text())\n"
            f"assert repair['base']=={self.base!r}\n"
            "assert 'float(a)' in Path(repair['base_diff']).read_text()\n"
            "assert 'upstream.txt' in Path(repair['base_diff']).read_text()\n"
            "conflict=next(x for x in repair['conflicts'] if x['path']=='calc.py')\n"
            "versions={k:Path(v['path']).read_text() for k,v in conflict['versions'].items()}\n"
            "assert 'NotImplementedError' in versions['ancestor']\n"
            "assert 'return a + b' in versions['candidate']\n"
            "assert 'return float(a) + float(b)' in versions['base']\n"
            "assert '<<<<<<<' in Path('calc.py').read_text()\n"
            "assert '|||||||' in Path('calc.py').read_text()\n"
            "assert 'Upstream addition' in Path('upstream.txt').read_text()\n"
            # Deliberately omit verification/publish and ask for land: host must require review.
            f"print(json.dumps({{'summary':'Resolved both sides','changes':[{{'path':'calc.py','content':{RESOLVED!r}}}],'next':[{{'kind':'land','purpose':'Try skipping review'}}]}}))\n"
        )
        self.e.config["worker"] = {"type": "command", "argv": [sys.executable, str(adapter)]}
        self.e.execute(task)
        self.assertFalse(integration.merge_head(workspace), self.s.snapshot()["decisions"])
        queued = [t for t in self.s.snapshot()["tasks"] if t["status"] == "queued"]
        self.assertEqual([t["kind"] for t in queued], ["review"])
        self.assertIsNone(self.s.track("addition")["review"])
        self.assertEqual(command(["git", "rev-parse", "origin/main"], self.repo), self.base)
        Engine(self.s).run(max_tasks=5)
        self.assert_delivered()

    def test_combined_failure_repairs_the_merged_tree_even_without_text_conflicts(self):
        self.ready(conflict=False)
        task, workspace = self.repair_task()
        self.assertIn(
            "Combined verification failed", integration.pending(self.s.track("addition"))["reason"]
        )

        def worker(config, context, *args):
            self.assertEqual(context["integration_repair"]["conflicts"], [])
            self.assertTrue((Path(context["workspace"]) / "test_upstream.py").exists())
            return {
                "summary": "Preserve numeric contract",
                "changes": [{"path": "calc.py", "content": RESOLVED}],
            }

        with patch("todo_flow.engine.run_worker", side_effect=worker):
            self.e.execute(task)
        Engine(self.s).run(max_tasks=5)
        self.assert_delivered()

    def test_question_resumes_merge_in_new_engine_without_discarding_markers(self):
        self.ready()
        task, workspace = self.repair_task()
        with patch(
            "todo_flow.engine.run_worker",
            return_value={"summary": "Need intent", "question": "Keep numeric-only inputs?"},
        ):
            self.e.execute(task)
        markers = (workspace / "calc.py").read_bytes()
        decision = self.s.snapshot()["decisions"][0]
        self.s.answer(decision["id"], "Yes")
        resumed = self.s.claim("successor")
        engine = Engine(self.s)

        def worker(config, context, *args):
            self.assertEqual((workspace / "calc.py").read_bytes(), markers)
            self.assertEqual(context["integration_repair"]["base"], self.base)
            return {"summary": "Resolved", "changes": [{"path": "calc.py", "content": RESOLVED}]}

        with patch("todo_flow.engine.run_worker", side_effect=worker):
            engine.execute(resumed)
        engine.run(max_tasks=5)
        self.assert_delivered()

    def test_recovery_after_merge_commit_before_state_update(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        self.e.apply_changes(task, workspace, [{"path": "calc.py", "content": RESOLVED}], record)
        committed = command(["git", "rev-parse", "HEAD"], workspace)
        with patch(
            "todo_flow.engine.run_worker", return_value={"summary": "Merge already resolved"}
        ):
            Engine(self.s).execute(task)
        self.assertEqual(self.s.track("addition")["head"], committed)
        Engine(self.s).run(max_tasks=5)
        self.assert_delivered()

    def test_unresolved_or_marker_proposals_do_not_commit_and_verification_is_blocked(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        for changes in ([], [{"path": "calc.py", "content": (workspace / "calc.py").read_text()}]):
            with self.assertRaises(Conflict):
                self.e.apply_changes(task, workspace, changes, record)
        with self.assertRaisesRegex(Conflict, "resolved merge"):
            self.e.verify(task, workspace)
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), self.before["head"])
        self.assertTrue(integration.unmerged(workspace))
        command(["git", "merge", "--abort"], workspace)
        with self.assertRaisesRegex(Conflict, "merge changed"):
            self.e.apply_changes(
                task, workspace, [{"path": "calc.py", "content": RESOLVED}], record
            )
        self.assertEqual(command(["git", "status", "--porcelain"], workspace), "")

    def test_dirty_checkout_is_preserved_before_preparing_repair(self):
        self.ready()
        task, workspace = self.repair_task()
        (workspace / "calc.py").write_text("User edit\n")
        with self.assertRaisesRegex(Conflict, "clean owned checkout"):
            integration.prepare(self.e, task, workspace)
        self.assertEqual((workspace / "calc.py").read_text(), "User edit\n")
        self.assertIsNone(integration.merge_head(workspace))

    def test_repair_fetches_new_base_then_pins_it_across_interruption(self):
        self.ready()
        task, workspace = self.repair_task()
        (self.repo / "newer.txt").write_text("Arrived before repair\n")
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Another base advance"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        latest = command(["git", "rev-parse", "HEAD"], self.repo)
        record = integration.prepare(self.e, task, workspace)
        self.assertEqual(record["base"], latest)
        self.assertEqual((workspace / "newer.txt").read_text(), "Arrived before repair\n")
        (self.repo / "later.txt").write_text("Arrived during repair\n")
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Advance while repair is paused"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        command(["git", "fetch", "origin", "main"], self.repo)
        resumed = integration.prepare(Engine(self.s), task, workspace)
        self.assertEqual(resumed["base"], latest)
        self.assertEqual(integration.merge_head(workspace), latest)
        self.assertFalse((workspace / "later.txt").exists())
        self.assertNotIn("later.txt", Path(resumed["base_diff"]).read_text())

    def test_commits_merge_even_when_resolution_keeps_candidate_tree(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        # Choose the exact candidate versions for all changed paths. The staged tree now
        # matches HEAD, but the merge parent must still be recorded (no squash/no-op commit).
        command(["git", "restore", "--source=HEAD", "--staged", "--worktree", "."], workspace)
        self.assertEqual(command(["git", "diff", "--cached", "--name-only"], workspace), "")
        self.e.apply_changes(task, workspace, [], record)
        integration.finish_repair(self.e, task, workspace, record)
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [self.before["head"], self.base],
        )

    def test_landing_retry_recovers_persisted_repair_intent(self):
        self.ready()
        task, workspace = self.repair_task()
        before = integration.pending(self.s.track("addition"))
        followup = Engine(self.s).land(task)
        self.assertEqual([row["kind"] for row in followup["next"]], ["work"])
        self.assertEqual(integration.pending(self.s.track("addition")), before)
        self.assertIsNone(integration.merge_head(workspace))

    def test_git_error_without_unmerged_paths_does_not_schedule_conflict_work(self):
        self.ready()
        # Use the known reviewed candidate in a fresh fixture; make the merge command fail itself.
        with self.s.transaction() as c:
            c.execute(
                "UPDATE tracks SET review=?,verification=?,landing=NULL WHERE id='addition'",
                (self.before["review"], self.before["verification"]),
            )
        task, _ = self.repair_task()
        from todo_flow import engine as module

        original = module.command

        def fail_merge(argv, *args, **kwargs):
            if argv[:2] == ["git", "merge"]:
                raise RuntimeError("Simulated Git execution failure")
            return original(argv, *args, **kwargs)

        with patch("todo_flow.engine.command", side_effect=fail_merge):
            with self.assertRaisesRegex(RuntimeError, "Simulated Git"):
                self.e.land(task)
        self.assertIsNone(self.s.track("addition")["landing"])
