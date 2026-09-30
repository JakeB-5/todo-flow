import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_flow
from todo_flow import integration, obsolete_integration
from todo_flow.adapters import command
from todo_flow.cleanup import cleanup_track, receipt_path
from todo_flow.engine import Engine
from todo_flow.obsolete_integration import evidence_path
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

    def ready(self, conflict=True, add_upstream=True):
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
        if add_upstream:
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

    def assert_delivered(self, expected_errors=0):
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
        self.assertEqual(
            sum(e["type"] == "attempt.error" for e in self.s.snapshot()["events"]),
            expected_errors,
        )

    def test_real_conflict_worker_reads_both_sides_and_new_head_is_reviewed_before_landing(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        original = obsolete_integration.read_evidence(self.s.path, old)
        self.assertEqual(original["base"], self.base)
        self.assertEqual(original["candidate"], self.before["head"])
        self.assertEqual(original["observed"], obsolete_integration.observe(old))
        self.assertEqual(
            base64.b64decode(original["observed"]["files"]["calc.py"]["data"]),
            (old / "calc.py").read_bytes(),
        )
        for row in original["observed"]["index"].split("\0"):
            if row:
                blob = row.split("\t", 1)[0].split()[1]
                self.assertEqual(
                    base64.b64decode(original["observed"]["blobs"][blob]),
                    obsolete_integration.git_bytes(old, "cat-file", "blob", blob),
                )
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
        self.assertFalse(old.exists())
        evidence = obsolete_integration.read_evidence(self.s.path, old)
        self.assertEqual(evidence["observed"], original["observed"])
        self.assertEqual(evidence["resolution"]["head"], self.s.track("addition")["head"])
        report = json.loads(receipt_path(self.s, self.s.track("addition")).read_text())
        removed = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(removed["obsolete"]["path"], str(evidence_path(self.s.path, old)))

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
        original = obsolete_integration.read_evidence(self.s.path, record["integration"])
        self.assertEqual(original["base"], self.base)
        self.e.apply_changes(task, workspace, [{"path": "calc.py", "content": RESOLVED}], resumed)
        integration.finish_repair(self.e, task, workspace, resumed)
        evidence = obsolete_integration.read_evidence(self.s.path, record["integration"])
        self.assertEqual(evidence["observed"], original["observed"])
        self.assertEqual(evidence["base"], self.base)
        self.assertEqual(evidence["resolution"]["parents"], [self.before["head"], latest])

    def test_commits_merge_even_when_resolution_keeps_candidate_tree(self):
        self.ready(add_upstream=False)
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        # The resolved tree matches HEAD, but both merge parents must still be recorded.
        self.e.apply_changes(
            task,
            workspace,
            [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}],
            record,
        )
        integration.finish_repair(self.e, task, workspace, record)
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [self.before["head"], self.base],
        )

    def test_repair_preserves_unrelated_staging_before_proposal_or_resumed_worker(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        notes = workspace / "operator-notes.txt"
        notes.write_text("Operator note\n")
        command(["git", "add", "operator-notes.txt"], workspace)
        index = command(["git", "ls-files", "--stage", "-z"], workspace)
        with self.assertRaisesRegex(Conflict, "checkout/index changed"):
            self.e.apply_changes(
                task, workspace, [{"path": "calc.py", "content": RESOLVED}], record
            )
        with self.assertRaisesRegex(Conflict, "checkout/index changed"):
            integration.prepare(Engine(self.s), task, workspace)
        self.assertEqual(command(["git", "ls-files", "--stage", "-z"], workspace), index)
        self.assertEqual(notes.read_text(), "Operator note\n")
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), self.before["head"])

    def test_landing_retry_recovers_persisted_repair_intent(self):
        self.ready()
        task, workspace = self.repair_task()
        before = integration.pending(self.s.track("addition"))
        followup = Engine(self.s).land(task)
        self.assertEqual([row["kind"] for row in followup["next"]], ["work"])
        self.assertEqual(integration.pending(self.s.track("addition")), before)
        self.assertIsNone(integration.merge_head(workspace))

    def finish_with_cleanup_outage(self):
        task, _workspace = self.repair_task()
        with patch(
            "todo_flow.engine.run_worker",
            return_value={
                "summary": "Resolved",
                "changes": [{"path": "calc.py", "content": RESOLVED}],
            },
        ):
            self.e.execute(task)
        with patch("todo_flow.cleanup.cleanup_track", side_effect=OSError("Cleanup outage")):
            Engine(self.s).run(max_tasks=5)
        self.assert_delivered()

    def test_obsolete_cleanup_preserves_changed_files_parents_index_and_identity(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.finish_with_cleanup_outage()
        archived = obsolete_integration.read_evidence(self.s.path, old)
        source = old / "calc.py"
        source.write_text("User changes after the original conflict\n")
        report = cleanup_track(self.s, "addition")
        item = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(item["status"], "preserved")
        self.assertEqual(source.read_text(), "User changes after the original conflict\n")
        self.assertEqual(obsolete_integration.read_evidence(self.s.path, old), archived)

    def test_obsolete_cleanup_preserves_changed_merge_parent(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.finish_with_cleanup_outage()
        gitdir = Path(command(["git", "rev-parse", "--absolute-git-dir"], old))
        (gitdir / "MERGE_HEAD").write_text(self.base + "\n")
        report = cleanup_track(self.s, "addition")
        item = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(item["status"], "preserved")
        self.assertTrue(old.exists())

    def test_obsolete_cleanup_preserves_changed_index(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.finish_with_cleanup_outage()
        command(["git", "add", "calc.py"], old)
        report = cleanup_track(self.s, "addition")
        item = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(item["status"], "preserved")
        self.assertTrue(old.exists())

    def test_obsolete_cleanup_preserves_replacement_checkout(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.finish_with_cleanup_outage()
        moved = old.with_name(old.name + "-user-moved")
        command(["git", "worktree", "move", str(old), str(moved)], self.repo)
        command(["git", "worktree", "add", "--detach", str(old), self.base], self.repo)
        report = cleanup_track(self.s, "addition")
        item = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(item["status"], "preserved")
        self.assertTrue(old.exists())
        self.assertTrue(moved.exists())

    def test_original_evidence_failure_resumes_and_removes_same_integration(self):
        with patch("todo_flow.obsolete_integration.write_json", side_effect=OSError("Disk full")):
            land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.assertTrue(old.exists())
        self.assertTrue(integration.unmerged(old))
        self.assertIsNone(obsolete_integration.read_evidence(self.s.path, old))
        self.assertNotEqual(self.s.track("addition")["control"], "finished")
        pending = integration.pending(self.s.track("addition"))
        original = pending["original_evidence"]
        self.assertEqual(pending["integration"], str(old))
        self.assertEqual(original["base"], self.base)
        self.assertEqual(original["candidate"], self.before["head"])
        self.assertEqual(original["observed"], obsolete_integration.observe(old))
        with self.assertRaises(Conflict):
            cleanup_track(self.s, "addition")
        decision = next(d for d in self.s.snapshot()["decisions"] if d["status"] == "open")
        self.s.answer(decision["id"], "Storage restored; resume the preserved integration")
        task, _workspace = self.repair_task()

        def worker(config, context, *args):
            self.assertEqual(context["integration_repair"]["integration"], str(old))
            self.assertEqual(obsolete_integration.read_evidence(self.s.path, old), original)
            return {"summary": "Resolved", "changes": [{"path": "calc.py", "content": RESOLVED}]}

        with patch("todo_flow.engine.run_worker", side_effect=worker):
            Engine(self.s).execute(task)
        Engine(self.s).run(max_tasks=5)
        self.assert_delivered(expected_errors=1)
        self.assertFalse(old.exists())
        evidence = obsolete_integration.read_evidence(self.s.path, old)
        self.assertEqual(evidence["observed"], original["observed"])
        self.assertEqual(evidence["base"], original["base"])
        self.assertEqual(evidence["candidate"], original["candidate"])
        self.assertEqual(evidence["resolution"]["head"], self.s.track("addition")["head"])
        report = json.loads(receipt_path(self.s, self.s.track("addition")).read_text())
        self.assertEqual(report["status"], "complete")
        removed = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(removed["obsolete"]["path"], str(evidence_path(self.s.path, old)))

    def assert_capture_retry_preserves_change(self, change):
        with patch("todo_flow.obsolete_integration.write_json", side_effect=OSError("Disk full")):
            land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        pending = integration.pending(self.s.track("addition"))
        change(old)
        changed = obsolete_integration.observe(old)
        self.assertNotEqual(changed, pending["original_evidence"]["observed"])
        decision = next(d for d in self.s.snapshot()["decisions"] if d["status"] == "open")
        self.s.answer(decision["id"], "Storage restored; retry without discarding changes")
        task, workspace = self.repair_task()
        with patch("todo_flow.engine.run_worker") as worker:
            Engine(self.s).execute(task)
        worker.assert_not_called()
        self.assertEqual(integration.pending(self.s.track("addition")), pending)
        self.assertEqual(obsolete_integration.observe(old), changed)
        self.assertIsNone(obsolete_integration.read_evidence(self.s.path, old))
        self.assertIsNone(integration.merge_head(workspace))
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), self.before["head"])
        self.assertEqual(command(["git", "rev-parse", "origin/main"], self.repo), self.base)
        self.assertNotEqual(self.s.track("addition")["control"], "finished")
        return old

    def test_original_evidence_retry_preserves_changed_files(self):
        old = self.assert_capture_retry_preserves_change(
            lambda workspace: (workspace / "calc.py").write_text("User changes\n")
        )
        self.assertEqual((old / "calc.py").read_text(), "User changes\n")

    def test_original_evidence_retry_preserves_changed_parent(self):
        def change(workspace):
            gitdir = Path(command(["git", "rev-parse", "--absolute-git-dir"], workspace))
            (gitdir / "MERGE_HEAD").write_text(self.base + "\n")

        self.assert_capture_retry_preserves_change(change)

    def test_original_evidence_retry_preserves_replacement_checkout(self):
        def change(workspace):
            moved = workspace.with_name(workspace.name + "-user-moved")
            command(["git", "worktree", "move", str(workspace), str(moved)], self.repo)
            command(["git", "worktree", "add", "--detach", str(workspace), self.base], self.repo)

        old = self.assert_capture_retry_preserves_change(change)
        self.assertTrue(old.with_name(old.name + "-user-moved").exists())

    def test_resolution_evidence_failure_does_not_clear_pending_repair(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        self.e.apply_changes(task, workspace, [{"path": "calc.py", "content": RESOLVED}], record)
        with patch("todo_flow.obsolete_integration.write_json", side_effect=OSError("Disk full")):
            with self.assertRaisesRegex(OSError, "Disk full"):
                integration.finish_repair(self.e, task, workspace, record)
        self.assertIsNotNone(integration.pending(self.s.track("addition")))
        self.assertTrue(Path(record["integration"]).exists())
        with self.assertRaises(Conflict):
            cleanup_track(self.s, "addition")
        integration.finish_repair(self.e, task, workspace, record)
        self.assertIsNone(integration.pending(self.s.track("addition")))
        evidence = obsolete_integration.read_evidence(self.s.path, record["integration"])
        self.assertEqual(evidence["resolution"]["head"], self.s.track("addition")["head"])

    def test_obsolete_cleanup_recovers_lost_removal_response(self):
        land = self.ready()
        old = self.s.path / "integrations" / land["attempt"]
        self.finish_with_cleanup_outage()
        archive = obsolete_integration.read_evidence(self.s.path, old)
        removed = []

        def lose_response(argv, *args, **kwargs):
            result = command(argv, *args, **kwargs)
            if argv[:3] == ["git", "worktree", "remove"] and argv[-1] == str(old):
                removed.append(argv)
                raise OSError("Lost removal response")
            return result

        with patch("todo_flow.cleanup.command", side_effect=lose_response):
            report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "deferred")
        self.assertEqual(len(removed), 1)
        self.assertIn("--force", removed[0])
        self.assertFalse(old.exists())
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "complete")
        item = next(row for row in report["worktrees"] if row["path"] == str(old))
        self.assertEqual(item["status"], "removed")
        self.assertEqual(obsolete_integration.read_evidence(self.s.path, old), archive)
        self.assertEqual(cleanup_track(self.s, "addition")["status"], "complete")

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
