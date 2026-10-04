import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_flow
from test_flow import DOC
from todo_flow import clean_integration, cleanup, integration
from todo_flow.adapters import command
from todo_flow.cleanup import cleanup_track, receipt_path, terminal_cleanup
from todo_flow.engine import Engine
from todo_flow.store import encode


class CleanupTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def finish(self, auto=False):
        self.s.start("addition")
        engine = Engine(self.s)
        engine.config["cleanup_on_complete"] = auto
        engine.run(max_tasks=10)
        self.assertEqual(
            self.s.track("addition")["status"],
            "done",
            encode(self.s.snapshot()["decisions"]),
        )
        return engine

    def test_parallel_completion_removes_all_checkouts_and_preserves_evidence(self):
        self.s.register({**DOC, "id": "second"})
        self.s.start("addition")
        self.s.start("second")
        Engine(self.s).run(jobs=2, max_tasks=20)
        worktrees = command(["git", "worktree", "list", "--porcelain"], self.repo)
        snapshot = self.s.snapshot()
        receipts = {}
        for track in snapshot["tracks"]:
            path = receipt_path(self.s, track)
            receipts[track["id"]] = (
                json.loads(path.read_text()) if path.exists() else {"missing": str(path)}
            )
        diagnostics = encode(
            {
                "tracks": snapshot["tracks"],
                "tasks": snapshot["tasks"],
                "decisions": snapshot["decisions"],
                "cleanup_events": [
                    event for event in snapshot["events"] if event["type"].startswith("cleanup.")
                ],
                "receipts": receipts,
                "git_worktrees": worktrees,
            }
        )
        print("Parallel completion diagnostics: " + diagnostics, flush=True)
        self.assertEqual(worktrees.count("worktree "), 1, diagnostics)
        for track in snapshot["tracks"]:
            self.assertEqual(track["status"], "done", diagnostics)
            report = receipts[track["id"]]
            self.assertEqual(report.get("status"), "complete", diagnostics)
            self.assertGreaterEqual(len(report["worktrees"]), 3)
            self.assertTrue((self.s.path / "tracks" / track["id"] / "track.html").exists())
            self.assertEqual(
                command(["git", "rev-parse", track["branch"]], self.repo), track["head"]
            )
        self.assertTrue(list((self.s.path / "attempts").glob("*/output.json")))
        self.assertEqual(Engine(self.s).run(), 0)

    def test_dry_run_dirty_ignored_and_unlanded_head_are_preserved(self):
        self.finish()
        track = self.s.track("addition")
        workspace = Path(track["workspace"])
        planned = cleanup_track(self.s, "addition", dry_run=True)
        self.assertEqual(planned["status"], "complete")
        self.assertTrue(workspace.exists())
        self.assertFalse(receipt_path(self.s, track).exists())
        for filename in ("calc.py", "notes.txt", "operator.local"):
            original = (
                (workspace / filename).read_text() if (workspace / filename).exists() else None
            )
            if filename.endswith(".local"):
                with (self.repo / ".git/info/exclude").open("a") as file:
                    file.write("\n*.local\n")
            (workspace / filename).write_text("Keep user data")
            plan = cleanup_track(self.s, "addition", dry_run=True)
            self.assertEqual(
                next(r for r in plan["worktrees"] if r["path"] == str(workspace))["status"],
                "preserved",
            )
            self.assertEqual((workspace / filename).read_text(), "Keep user data")
            if original is None:
                (workspace / filename).unlink()
            else:
                (workspace / filename).write_text(original)
        (workspace / "calc.py").write_text("# New user commit after delivery\n")
        command(["git", "add", "calc.py"], workspace)
        command(["git", "commit", "-m", "Preserve unlanded user work"], workspace)
        user_head = command(["git", "rev-parse", "HEAD"], workspace)
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "deferred")
        self.assertTrue(workspace.exists())
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), user_head)
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_cleanup_failure_retries_without_reopening_finished_work(self):
        with patch("todo_flow.cleanup.cleanup_track", side_effect=OSError("Temporary outage")):
            self.finish(auto=True)
        snapshot = self.s.snapshot()
        self.assertTrue(any(e["type"] == "cleanup.deferred" for e in snapshot["events"]))
        self.assertFalse(any(e["type"] == "attempt.error" for e in snapshot["events"]))
        self.assertEqual(Engine(self.s).run(), 0)
        self.assertEqual(
            command(["git", "worktree", "list", "--porcelain"], self.repo).count("worktree "), 1
        )

    def test_removal_before_receipt_is_recoverable(self):
        self.finish()
        removed = []
        original = command

        def lose_receipt(argv, *args, **kwargs):
            result = original(argv, *args, **kwargs)
            if argv[:3] == ["git", "worktree", "remove"] and not removed:
                removed.append(argv[-1])
                raise OSError("Lost response after Git removed the checkout")
            return result

        with patch("todo_flow.cleanup.command", side_effect=lose_receipt):
            self.assertEqual(cleanup_track(self.s, "addition")["status"], "deferred")
        self.assertFalse(Path(removed[0]).exists())
        self.assertEqual(cleanup_track(self.s, "addition")["status"], "complete")
        self.assertEqual(cleanup_track(self.s, "addition")["status"], "complete")

    def test_review_only_candidate_is_retained(self):
        self.s.start("addition")
        engine = Engine(self.s)
        engine.config["endpoint"] = "review"
        # The test adapter asks to land, so finish from the independently reviewed candidate.
        engine.run(max_tasks=3)
        with self.s.transaction() as c:
            c.execute(
                "UPDATE tasks SET status='superseded' WHERE track=? AND status='queued'",
                ("addition",),
            )
            c.execute("UPDATE tracks SET control='finished' WHERE id=?", ("addition",))
        track = self.s.track("addition")
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "deferred")
        self.assertTrue(Path(track["workspace"]).exists())

    def test_main_checkout_cannot_be_removed_by_a_bad_workspace_reference(self):
        self.finish()
        with self.s.transaction() as c:
            c.execute("UPDATE tracks SET workspace=? WHERE id=?", (str(self.repo), "addition"))
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "deferred")
        self.assertTrue((self.repo / ".git").exists())

    def superseded_integration(self, *, different_tree=False, missing_parent=False):
        self.s.start("addition")
        engine = Engine(self.s)
        engine.config["cleanup_on_complete"] = False
        engine.run(max_tasks=3)
        (self.repo / "upstream.txt").write_text("Included upstream\n")
        command(["git", "add", "upstream.txt"], self.repo)
        command(["git", "commit", "-m", "Independent upstream"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        base = command(["git", "rev-parse", "HEAD"], self.repo)
        land = self.s.claim("interrupted-lander")
        self.assertEqual(land["kind"], "land")
        # Interrupt after the host creates its merge, before verification/publication.
        with patch.object(engine, "verify", side_effect=RuntimeError("Interrupted verifier")):
            with self.assertRaisesRegex(RuntimeError, "Interrupted verifier"):
                engine.land(land)
        old = self.s.path / "integrations" / land["attempt"]
        head = command(["git", "rev-parse", "HEAD"], old)
        if missing_parent:
            # Same upstream tree, different history in this disposable local remote.
            replacement = command(
                [
                    "git",
                    "commit-tree",
                    base + "^{tree}",
                    "-p",
                    base + "^",
                    "-m",
                    "Replacement upstream",
                ],
                self.repo,
            )
            command(["git", "reset", "--hard", replacement], self.repo)
            command(["git", "push", "--force", "origin", "main"], self.repo)
        result = integration.request_repair(engine, land, old, base, "Retry interrupted merge")
        self.s.finish(land, result)
        if different_tree:
            repair = self.s.claim("repairer")
            with patch(
                "todo_flow.engine.run_worker",
                return_value={
                    "summary": "Repair with a distinct tree",
                    "changes": [
                        {
                            "path": "calc.py",
                            "content": "def add(a, b):\n    return a + b\n# Later change\n",
                        }
                    ],
                },
            ):
                engine.execute(repair)
        engine.run(max_tasks=10)
        self.assertEqual(self.s.track("addition")["status"], "done", self.s.snapshot()["decisions"])
        # Real Git counterexample: repair reverses the parents. The old merge is
        # not an ancestor even when its exact tree and both parents are delivered.
        self.assertFalse(clean_integration.is_ancestor(self.repo, head, "origin/main"))
        return old, head

    def retirement_item(self, report, path):
        return next(row for row in report["worktrees"] if row["path"] == str(path))

    def assert_retired(self, path, head, report):
        item = self.retirement_item(report, path)
        self.assertEqual(item["status"], "removed", item)
        proof = item["retirement"]
        self.assertEqual(command(["git", "rev-parse", proof["ref"]], self.repo), head)
        self.assertEqual(
            command(["git", "show", proof["ref"] + ":upstream.txt"], self.repo),
            "Included upstream",
        )
        self.assertFalse(path.exists())
        self.assertFalse(Path(item["target"]["checkout"]["gitdir"]).exists())
        self.assertNotIn(str(path), cleanup.registered_worktrees(self.repo))
        # These integrations are plain Git resources; managed candidates continue
        # through the existing Orca inventory/ownership gate, covered separately.
        self.assertNotIn("orca", item)
        saved = json.loads(receipt_path(self.s, self.s.track("addition")).read_text())
        self.assertEqual(self.retirement_item(saved, path)["retirement"], proof)
        return proof

    def test_superseded_clean_merge_is_retired_with_recoverable_commit(self):
        old, head = self.superseded_integration()
        track = self.s.track("addition")
        self.assertEqual(
            command(["git", "rev-parse", head + "^{tree}"], self.repo),
            command(["git", "rev-parse", track["head"] + "^{tree}"], self.repo),
        )
        for parent in command(["git", "show", "-s", "--format=%P", head], self.repo).split():
            command(["git", "merge-base", "--is-ancestor", parent, "origin/main"], self.repo)
        # clean-merge-retirement: formerly preserved solely for non-ancestry;
        # equivalence + inclusion authorize removal, with the commit still reachable.
        plan = cleanup_track(self.s, "addition", dry_run=True)
        item = self.retirement_item(plan, old)
        self.assertEqual(item["status"], "would-remove", item)
        self.assertTrue(old.exists())
        self.assertFalse(receipt_path(self.s, track).exists())
        self.assertEqual(
            command(["git", "for-each-ref", "refs/todo-flow/retired-integrations/"], self.repo),
            "",
        )
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "complete", report)
        self.assert_retired(old, head, report)

    def test_superseded_merge_requires_included_tree(self):
        old, _ = self.superseded_integration(different_tree=True)
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("tree differs", item["reason"])
        self.assertTrue(old.exists())

    def test_superseded_merge_requires_both_original_parents(self):
        old, head = self.superseded_integration(missing_parent=True)
        self.assertEqual(
            command(["git", "rev-parse", head + "^{tree}"], self.repo),
            command(["git", "rev-parse", self.s.track("addition")["head"] + "^{tree}"], self.repo),
        )
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("parent is not included", item["reason"])
        self.assertTrue(old.exists())

    def test_superseded_merge_path_and_shape_do_not_establish_ownership(self):
        old, _ = self.superseded_integration()
        clean_integration.creation_path(self.s, old).unlink()
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("creation evidence", item["reason"])
        self.assertTrue(old.exists())

    def test_superseded_merge_preserves_file_and_index_changes(self):
        old, _ = self.superseded_integration()
        source = old / "calc.py"
        original = source.read_text()
        for staged in (False, True):
            with self.subTest(staged=staged):
                source.write_text("# User work\n")
                if staged:
                    command(["git", "add", "calc.py"], old)
                    source.write_text(original)  # Index-only edit must also block removal.
                status = command(["git", "status", "--porcelain"], old)
                report = cleanup_track(self.s, "addition", dry_run=True)
                item = self.retirement_item(report, old)
                self.assertEqual(item["status"], "preserved")
                self.assertIn("changes", item["reason"])
                self.assertEqual(command(["git", "status", "--porcelain"], old), status)
                command(["git", "restore", "--staged", "--worktree", "calc.py"], old)

    def test_superseded_merge_preserves_replacement_checkout_with_identical_head(self):
        old, head = self.superseded_integration()
        moved = old.with_name(old.name + "-user-moved")
        command(["git", "worktree", "move", str(old), str(moved)], self.repo)
        command(["git", "worktree", "add", "--detach", str(old), head], self.repo)
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("target changed", item["reason"])
        self.assertTrue(old.exists())
        self.assertTrue(moved.exists())

    def test_superseded_merge_rechecks_user_changes_immediately_before_removal(self):
        old, head = self.superseded_integration()
        original = cleanup.reclaim
        source = old / "calc.py"
        text = source.read_text()

        def change_after_archive(directory, track, workspace, *, dry_run=False):
            result = original(directory, track, workspace, dry_run=dry_run)
            if Path(workspace) == old and not dry_run:
                source.write_text("# User edit after retirement intent\n")
            return result

        with patch("todo_flow.cleanup.reclaim", side_effect=change_after_archive):
            report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("changes", item["reason"])
        self.assertEqual(source.read_text(), "# User edit after retirement intent\n")
        source.write_text(text)
        self.assert_retired(old, head, cleanup_track(self.s, "addition"))

    def recover_retirement(self, *, after_remove):
        old, head = self.superseded_integration()
        removed, interrupted = [], []
        destination = receipt_path(self.s, self.s.track("addition"))
        original = cleanup.reclaim

        def interrupt_after_archive(directory, track, workspace, *, dry_run=False):
            if Path(workspace) == old and not dry_run and not interrupted and not after_remove:
                saved = json.loads(destination.read_text())
                item = self.retirement_item(saved, old)
                self.assertTrue(item["removalIntent"])
                self.assertEqual(
                    command(["git", "rev-parse", item["retirement"]["ref"]], self.repo), head
                )
                interrupted.append(True)
                raise OSError("Interrupted after durable preservation")
            return original(directory, track, workspace, dry_run=dry_run)

        def lose_response(argv, *args, **kwargs):
            result = command(argv, *args, **kwargs)
            if argv[:3] == ["git", "worktree", "remove"] and argv[-1] == str(old):
                self.assertNotIn("--force", argv)
                removed.append(argv)
                if after_remove and not interrupted:
                    interrupted.append(True)
                    raise OSError("Lost removal response")
            return result

        with (
            patch("todo_flow.cleanup.reclaim", side_effect=interrupt_after_archive),
            patch("todo_flow.cleanup.command", side_effect=lose_response),
        ):
            first = cleanup_track(self.s, "addition")
            self.assertEqual(first["status"], "deferred", first)
            self.assertEqual(old.exists(), not after_remove)
            proof = self.retirement_item(first, old)["retirement"]
            for _ in range(2):
                resumed = cleanup_track(self.s, "addition")
                self.assertEqual(resumed["status"], "complete", resumed)
                self.assertEqual(self.assert_retired(old, head, resumed), proof)
        self.assertEqual(len(removed), 1)

    def test_superseded_merge_resumes_after_preservation_before_removal(self):
        self.recover_retirement(after_remove=False)

    def test_superseded_merge_resumes_after_lost_removal_response(self):
        self.recover_retirement(after_remove=True)

    def test_absent_superseded_merge_still_requires_preserved_ref_and_creation_record(self):
        old, head = self.superseded_integration()
        proof = self.assert_retired(old, head, cleanup_track(self.s, "addition"))
        command(["git", "update-ref", "-d", proof["ref"], head], self.repo)
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("preservation ref", item["reason"])
        self.assertEqual(report["status"], "deferred")
        command(["git", "update-ref", proof["ref"], head], self.repo)
        path = clean_integration.creation_path(self.s, old)
        original = path.read_bytes()
        path.unlink()
        report = cleanup_track(self.s, "addition")
        item = self.retirement_item(report, old)
        self.assertEqual(item["status"], "preserved")
        self.assertIn("creation evidence", item["reason"])
        path.write_bytes(original)
        self.assert_retired(old, head, cleanup_track(self.s, "addition"))

    def test_orca_closes_only_the_completed_unchanged_terminal(self):
        folder = self.root / "terminal-fixture"
        folder.mkdir()
        terminal = {
            "handle": "term-owned",
            "ptyId": "pty-owned",
            "incarnationId": "instance-owned",
            "worktreeId": "repo::path",
            "title": "TODO owned",
        }
        launch = {
            "backend": "orca",
            "terminal": terminal,
            "worktree": "id:repo::path",
            "cli": "orca",
            "repo": str(self.repo),
        }
        (folder / "launch.json").write_text(encode(launch))
        (folder / "terminal-process.json").write_text(
            encode({"status": "exited", "returncode": 0, "finished_at": time.time()})
        )
        current = {
            **terminal,
            "lastOutputAt": int(time.time() * 1000),
            "preview": "TODO Flow worker exited: 0\nuser@host %",
        }
        for changed in (
            {"title": "User session"},
            {"incarnationId": "reused"},
            {"lastOutputAt": (time.time() + 10) * 1000},
            {"preview": "TODO Flow worker exited: 0\nuser@host % sleep 100"},
        ):
            with patch(
                "todo_flow.cleanup.orca_result",
                return_value={"terminals": [{**current, **changed}]},
            ) as api:
                self.assertEqual(terminal_cleanup(folder, False)["status"], "preserved")
                self.assertEqual(api.call_count, 1)
        with patch(
            "todo_flow.cleanup.orca_result",
            side_effect=[{"terminals": [current]}, {"close": {"ptyKilled": True}}],
        ) as api:
            self.assertEqual(terminal_cleanup(folder, False)["status"], "closed")
            self.assertIn("term-owned", api.call_args.args[1])
        (folder / "terminal-process.json").write_text(
            encode({"status": "exited", "returncode": 1, "finished_at": time.time()})
        )
        exited = {**current, "preview": "TODO Flow worker exited: 1\nuser@host %"}
        with patch(
            "todo_flow.cleanup.orca_result",
            side_effect=[{"terminals": [exited]}, {"close": {"ptyKilled": True}}],
        ):
            self.assertEqual(terminal_cleanup(folder, False)["status"], "closed")
        (folder / "launch.json").write_text(
            encode(
                {
                    "backend": "tmux",
                    "handle": "@7",
                    "title": "owned",
                    "socket": "/tmp/test-tmux-socket",
                }
            )
        )
        with patch("todo_flow.cleanup.command", side_effect=["@7", "owned", "1", ""]) as commands:
            self.assertEqual(terminal_cleanup(folder, False)["status"], "closed")
            for call in commands.call_args_list:
                self.assertEqual(call.args[0][:3], ["tmux", "-S", "/tmp/test-tmux-socket"])
