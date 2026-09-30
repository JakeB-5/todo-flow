"""Synthetic public Orca CLI responses with real creation receipts and local Git."""

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_cleanup
import test_managed_workspace
from todo_flow import managed_workspace as managed
from todo_flow.adapters import command
from todo_flow.cleanup import cleanup_track, receipt_path
from todo_flow.cleanup_orca import ref_value
from todo_flow.store import encode


class OrcaCleanupTests(unittest.TestCase):
    setUp = test_cleanup.CleanupTests.setUp
    tearDown = test_cleanup.CleanupTests.tearDown
    start = test_managed_workspace.ManagedWorkspaceTests.start

    def call(self, cli, args, root):
        if args[:2] == ["worktree", "ps"]:
            wt = self.shown["result"]["worktree"]
            rows = (
                []
                if self.gone
                else [
                    {
                        "workspaceKind": "git",
                        "worktreeId": wt["id"],
                        "repoId": wt["repoId"],
                        "hostId": "local",
                        "path": str(self.candidate),
                        "branch": wt["branch"],
                        "isMainWorktree": False,
                        "worktreeInstanceId": wt["instanceId"],
                        "isActive": False,
                        "liveTerminalCount": 0,
                        "hasAttachedPty": False,
                        "status": "inactive",
                        "agents": [],
                        "childWorktreeIds": [],
                        **self.row_changes,
                    }
                ]
            )
            return {
                "ok": True,
                "result": {
                    "worktrees": rows,
                    "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
                    "totalCount": len(rows),
                    "truncated": False,
                    **self.inventory_changes,
                },
            }
        if args[:2] == ["terminal", "list"]:
            return {"ok": True, "result": {"terminals": self.terminals}}
        if args[:2] == ["worktree", "rm"]:
            self.removals += 1
            wt = self.shown["result"]["worktree"]
            self.assertEqual(args, ["worktree", "rm", "--worktree", "id:" + wt["id"], "--json"])
            durable = self.entry(json.loads(receipt_path(self.s, self.track).read_text()))
            self.assertTrue(durable["orcaRemovalIntent"])
            self.assertEqual(ref_value(self.repo, durable["branchBackup"]), self.track["head"])
            if self.failure == "before-effect":
                raise TimeoutError("Synthetic transport uncertainty")
            command(["git", "worktree", "remove", str(self.candidate)], self.repo)
            # The public rm contract can delete the local branch. Exercise that
            # behavior rather than relying on a fixture that leaves it untouched.
            command(["git", "branch", "-D", self.track["branch"]], self.repo)
            self.gone = True
            if self.failure == "lost-response":
                raise TimeoutError("Synthetic lost deletion response")
            if self.failure == "interrupted":
                raise KeyboardInterrupt("Stopped before recording the deletion result")
            return {"ok": True, "result": {}}
        return test_managed_workspace.ManagedWorkspaceTests.call(self, cli, args, root)

    def finish_managed(self):
        self.gone = False
        self.removals = 0
        self.failure = None
        self.row_changes = {}
        self.inventory_changes = {}
        self.terminals = []
        self.start()
        self.engine.ensure_workspace(self.task)
        # Creation uses the real managed adapter; workers and verifier remain
        # the ordinary offline integration fixture, with no live Orca process.
        self.engine.config = self.s.config()
        self.engine.config["cleanup_on_complete"] = False
        self.engine.execute(self.task)
        self.engine.run(jobs=1, max_tasks=10)
        self.track = self.s.track("addition")
        self.assertEqual(self.track["status"], "done")
        self.assertTrue(self.candidate.exists())

    def entry(self, report):
        return next(row for row in report["worktrees"] if row["path"] == str(self.candidate))

    def cleanup(self, **kwargs):
        return cleanup_track(self.s, "addition", **kwargs)

    def assert_removed(self, report):
        self.assertEqual(report["status"], "complete", encode(report))
        self.assertEqual(report["landing"]["status"], "confirmed")
        self.assertTrue(report["branchesPreserved"])
        self.assertEqual(self.entry(report)["status"], "removed")
        self.assertFalse(self.candidate.exists())
        self.assertTrue(self.gone)
        self.assertNotIn(str(self.candidate), command(["git", "worktree", "list"], self.repo))
        self.assertEqual(
            ref_value(self.repo, "refs/heads/" + self.track["branch"]), self.track["head"]
        )
        command(["git", "cat-file", "-e", self.track["head"] + "^{commit}"], self.repo)
        self.assertTrue(list((self.s.path / "attempts").glob("*/output.json")))
        self.assertTrue(managed.receipt_path(self.gate).exists())

    def assert_preserved(self, report):
        self.assertEqual(report["status"], "deferred", encode(report))
        self.assertEqual(report["landing"]["status"], "confirmed")
        self.assertEqual(self.entry(report)["status"], "preserved", encode(report))
        self.assertTrue(self.candidate.exists())
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_managed_removal_preserves_branch_commits_and_execution_records(self):
        self.finish_managed()
        before = {p: p.read_bytes() for p in (self.s.path / "attempts").glob("*/output.json")}
        planned = self.cleanup(dry_run=True)
        self.assertEqual(self.entry(planned)["status"], "would-remove", encode(planned))
        self.assertEqual(self.removals, 0)
        self.assertFalse(receipt_path(self.s, self.track).exists())
        report = self.cleanup()
        self.assert_removed(report)
        self.assertEqual(self.removals, 1)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(json.loads(receipt_path(self.s, self.track).read_text()), report)
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)

    def test_lost_response_requeries_both_inventories_and_restores_branch(self):
        self.finish_managed()
        self.failure = "lost-response"
        self.assert_removed(self.cleanup())
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)

    def test_interruption_after_effect_recovers_without_a_second_remove(self):
        self.finish_managed()
        self.failure = "interrupted"
        with self.assertRaises(KeyboardInterrupt):
            self.cleanup()
        pending = json.loads(receipt_path(self.s, self.track).read_text())
        item = self.entry(pending)
        self.assertTrue(item["orcaRemovalIntent"])
        self.assertIsNone(ref_value(self.repo, "refs/heads/" + self.track["branch"]))
        self.assertEqual(ref_value(self.repo, item["branchBackup"]), self.track["head"])
        self.assert_removed(self.cleanup())
        self.assertEqual(self.entry(self.cleanup())["target"], item["target"])
        self.assertEqual(self.removals, 1)

    def test_recovery_waits_for_complete_inventory_after_deletion(self):
        self.finish_managed()
        self.failure = "interrupted"
        with self.assertRaises(KeyboardInterrupt):
            self.cleanup()
        self.inventory_changes = {"truncated": True}
        report = self.cleanup()
        self.assertEqual(report["status"], "deferred", encode(report))
        self.assertIn("incomplete", self.entry(report)["reason"])
        self.assertIsNone(ref_value(self.repo, "refs/heads/" + self.track["branch"]))
        self.inventory_changes = {}
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)

    def test_uncertain_effect_is_preserved_without_blind_redispatch(self):
        self.finish_managed()
        self.failure = "before-effect"
        self.assert_preserved(self.cleanup())
        again = self.cleanup()
        self.assert_preserved(again)
        self.assertIn("unconfirmed", self.entry(again)["reason"])
        self.assertEqual(self.removals, 1)

    def test_active_sessions_and_unowned_terminal_are_preserved(self):
        self.finish_managed()
        for changes in (
            {"liveTerminalCount": 1, "hasAttachedPty": True, "status": "active"},
            {"agents": [{"state": "working"}]},
            {"agents": [{"state": "idle"}]},
            {"childWorktreeIds": ["another-owned-elsewhere"]},
        ):
            with self.subTest(changes=changes):
                self.row_changes = changes
                self.assert_preserved(self.cleanup())
        self.row_changes = {}
        self.terminals = [{"handle": "user-terminal"}]
        self.assert_preserved(self.cleanup())
        self.assertEqual(self.removals, 0)

    def test_incomplete_or_uncovered_inventory_never_proves_absence(self):
        self.finish_managed()
        for changes in (
            {"truncated": True},
            {"totalCount": 2},
            {"hostScope": {"hostIds": [], "omittedHostIds": ["local"]}},
            {"worktrees": [], "totalCount": 0, "truncated": True},
        ):
            with self.subTest(changes=changes):
                self.inventory_changes = changes
                self.assert_preserved(self.cleanup())
        self.assertEqual(self.removals, 0)

    def test_changed_instance_and_user_files_prevent_deletion(self):
        self.finish_managed()
        self.row_changes = {"worktreeInstanceId": "replacement"}
        self.assert_preserved(self.cleanup())
        self.row_changes = {}
        notes = self.candidate / "notes.txt"
        notes.write_text("user notes")
        self.assert_preserved(self.cleanup())
        self.assertEqual(notes.read_text(), "user notes")
        self.assertEqual(self.removals, 0)

    def test_changed_ownership_receipt_blocks_pending_recovery(self):
        self.finish_managed()
        self.failure = "before-effect"
        self.assert_preserved(self.cleanup())
        path = managed.receipt_path(self.gate)
        receipt = json.loads(path.read_text())
        receipt["observation"]["instanceId"] = "replacement"
        path.write_text(encode(receipt))
        report = self.cleanup()
        self.assert_preserved(report)
        self.assertIn("ownership changed", self.entry(report)["reason"])
        self.assertEqual(self.removals, 1)

    def test_replaced_path_cannot_inherit_removal_intent(self):
        self.finish_managed()
        self.failure = "before-effect"
        self.assert_preserved(self.cleanup())
        retained = self.candidate.with_name("retained-candidate")
        self.candidate.rename(retained)
        shutil.copytree(retained, self.candidate)
        report = self.cleanup()
        self.assert_preserved(report)
        self.assertIn("target identity", self.entry(report)["reason"])
        self.assertTrue((retained / "calc.py").exists())
        self.assertEqual(self.removals, 1)

    def test_replacement_branch_is_not_overwritten_during_recovery(self):
        self.finish_managed()
        self.failure = "interrupted"
        with self.assertRaises(KeyboardInterrupt):
            self.cleanup()
        replacement = self.entry(json.loads(receipt_path(self.s, self.track).read_text()))["orca"][
            "observation"
        ]["base"]
        command(["git", "update-ref", "refs/heads/" + self.track["branch"], replacement], self.repo)
        report = self.cleanup()
        self.assertEqual(report["status"], "deferred", encode(report))
        self.assertFalse(report["branchesPreserved"])
        self.assertEqual(ref_value(self.repo, "refs/heads/" + self.track["branch"]), replacement)
        self.assertEqual(self.removals, 1)

    def test_local_git_route_never_requires_orca(self):
        test_cleanup.CleanupTests.finish(self)
        track = self.s.track("addition")
        with patch.object(managed, "_call") as api:
            report = self.cleanup()
        api.assert_not_called()
        self.assertEqual(report["status"], "complete", encode(report))
        self.assertFalse(Path(track["workspace"]).exists())
        self.assertEqual(ref_value(self.repo, "refs/heads/" + track["branch"]), track["head"])
