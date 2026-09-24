import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_flow
from test_flow import DOC
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
        self.assertEqual(self.s.track("addition")["status"], "done")
        return engine

    def test_parallel_completion_removes_all_checkouts_and_preserves_evidence(self):
        self.s.register({**DOC, "id": "second"})
        self.s.start("addition")
        self.s.start("second")
        Engine(self.s).run(jobs=2, max_tasks=20)
        worktrees = command(["git", "worktree", "list", "--porcelain"], self.repo)
        self.assertEqual(worktrees.count("worktree "), 1)
        for track in self.s.snapshot()["tracks"]:
            self.assertEqual(track["status"], "done")
            report = json.loads(receipt_path(self.s, track).read_text())
            self.assertEqual(report["status"], "complete", encode(report))
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
