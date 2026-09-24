import os
import signal
import sys
import time
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.store import Conflict
from todo_flow.verification import VerificationCleanupError

CODE = "def add(a, b):\n    return a + b\n"
REVIEW = {
    "summary": "Read exact candidate",
    "verdict": "met",
    "conditions": [{"id": "sum", "verdict": "met", "evidence": "calc.py"}],
    "next": [{"kind": "land", "purpose": "Land reviewed head"}],
}


class ExecutionBoundaryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def workspace(self):
        self.s.start("addition")
        task = self.s.claim("author")
        engine = Engine(self.s)
        engine.config["verify"] = [
            sys.executable,
            "-c",
            "from calc import add; assert add(2, 3) == 5",
        ]
        return engine, task, engine.ensure_workspace(task)

    def verified(self):
        engine, task, workspace = self.workspace()
        engine.apply_changes(task, workspace, [{"path": "calc.py", "content": CODE}])
        self.assertTrue(engine.verify(task, workspace)["ok"])
        return engine, task, workspace

    def test_proposal_commit_preserves_unrelated_staged_and_unstaged_changes(self):
        engine, task, workspace = self.workspace()
        notes = workspace / "operator-notes.txt"
        notes.write_text("Staged operator note\n")
        command(["git", "add", "operator-notes.txt"], workspace)
        staged = command(["git", "rev-parse", ":operator-notes.txt"], workspace)
        notes.write_text("Unstaged operator edit\n")
        engine.apply_changes(task, workspace, [{"path": "calc.py", "content": CODE}])
        self.assertEqual(
            command(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], workspace),
            "calc.py",
        )
        self.assertEqual(command(["git", "rev-parse", ":operator-notes.txt"], workspace), staged)
        self.assertEqual(notes.read_text(), "Unstaged operator edit\n")
        self.assertEqual(command(["git", "show", "HEAD:calc.py"], workspace), CODE.strip())

    def test_proposal_cannot_overwrite_existing_changes_to_its_own_file(self):
        engine, task, workspace = self.workspace()
        (workspace / "calc.py").write_text("# Operator recovery\n")
        with self.assertRaises(Conflict):
            engine.apply_changes(task, workspace, [{"path": "calc.py", "content": CODE}])
        self.assertEqual((workspace / "calc.py").read_text(), "# Operator recovery\n")

    def test_cached_verification_rejects_dirty_checkout(self):
        engine, task, workspace = self.verified()
        (workspace / "calc.py").write_text("def add(a,b): return 999\n")
        with self.assertRaises(Conflict):
            engine.verify(task, workspace)

    def test_review_never_starts_on_dirty_code(self):
        engine, task, workspace = self.verified()
        self.s.finish(task, {"summary": "Ready", "next": [{"kind": "review", "purpose": "Review"}]})
        review = self.s.claim("reviewer")
        (workspace / "calc.py").write_text("def add(a,b): return 999\n")
        with patch("todo_flow.engine.run_worker", return_value=REVIEW) as worker:
            engine.execute(review)
        worker.assert_not_called()
        self.assertIsNone(self.s.track("addition")["review"])
        with self.assertRaises(Conflict):
            engine.gate(review)

    def test_review_rejects_worktree_or_head_changes_during_read(self):
        for commit in (False, True):
            with self.subTest(commit=commit):
                # Each subcase owns its own fixture to avoid carrying a waiting task forward.
                case = ExecutionBoundaryTests()
                case.setUp()
                try:
                    engine, task, workspace = case.verified()
                    case.s.finish(
                        task,
                        {"summary": "Ready", "next": [{"kind": "review", "purpose": "Review"}]},
                    )
                    review = case.s.claim("reviewer")

                    def worker(*args):
                        (workspace / "calc.py").write_text("def add(a,b): return 999\n")
                        if commit:
                            command(["git", "add", "calc.py"], workspace)
                            command(["git", "commit", "-m", "Concurrent change"], workspace)
                        return REVIEW

                    with patch("todo_flow.engine.run_worker", side_effect=worker):
                        engine.execute(review)
                    self.assertIsNone(case.s.track("addition")["review"])
                    self.assertFalse(any(t["kind"] == "land" for t in case.s.snapshot()["tasks"]))
                finally:
                    case.tearDown()

    def test_direct_review_record_and_landing_gate_reject_dirty_candidate(self):
        engine, task, workspace = self.verified()
        engine.record_review(task, REVIEW)
        (workspace / "calc.py").write_text("def add(a,b): return 999\n")
        with self.assertRaises(Conflict):
            engine.record_review(task, REVIEW)
        with self.assertRaises(Conflict):
            engine.gate(task)

    def test_verification_rejects_head_moved_by_command(self):
        engine, task, workspace = self.workspace()
        engine.config["verify"] = ["git", "commit", "--allow-empty", "-m", "Moved by verification"]
        self.assertFalse(engine.verify(task, workspace)["ok"])

    def test_same_tree_new_head_gets_current_verification(self):
        engine, task, workspace = self.verified()
        command(["git", "commit", "--allow-empty", "-m", "New head"], workspace)
        head = command(["git", "rev-parse", "HEAD"], workspace)
        engine.update(task, head=head)
        self.assertEqual(engine.verify(task, workspace)["head"], head)

    def child_process_check(self, parent_waits):
        engine, task, workspace = self.workspace()
        pidfile = self.root / "child.pid"
        late = self.root / "late-write"
        child = (
            "import os,signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
            f"time.sleep(1.2)\nPath({str(late)!r}).write_text('escaped')\n"
        )
        parent = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            f"while not Path({str(pidfile)!r}).exists(): time.sleep(.01)\n"
            + ("time.sleep(20)\n" if parent_waits else "")
        )
        engine.config.update(verify=[sys.executable, "-c", parent], verify_timeout=0.3)
        try:
            record = engine.verify(task, workspace)
            self.assertFalse(record["ok"])
            self.assertEqual(record["error"], "TimeoutExpired" if parent_waits else "RuntimeError")
            self.assertTrue(pidfile.exists(), "Child must start before timeout for this regression")
            time.sleep(1.3)
            self.assertFalse(late.exists(), "Timed-out descendant still wrote a file")
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_timeout_kills_child_even_after_parent_exits_and_pipes_close(self):
        self.child_process_check(parent_waits=True)

    def test_successful_parent_cannot_leave_a_background_writer(self):
        self.child_process_check(parent_waits=False)

    def test_unconfirmed_process_cleanup_blocks_followup_instead_of_returning_failure(self):
        engine, task, _ = self.verified()
        engine.update(task, verification=None)
        self.s.finish(
            task, {"summary": "Recheck", "next": [{"kind": "verify", "purpose": "Verify"}]}
        )
        verification = self.s.claim("verifier")
        with patch(
            "todo_flow.engine.run_verification",
            side_effect=VerificationCleanupError("Cannot confirm exit"),
        ):
            engine.execute(verification)
        snapshot = self.s.snapshot()
        self.assertFalse(any(row["status"] == "queued" for row in snapshot["tasks"]))
        self.assertEqual(len(snapshot["decisions"]), 1)
        self.assertIn("Cannot confirm exit", snapshot["decisions"][0]["question"])
