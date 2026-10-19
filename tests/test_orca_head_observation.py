"""HEAD convergence regressions: synthetic Orca/clock, real local Git and Engine.

The registered conditions require old H -> host-pinned H to reuse one resource
and launch once. Actual identity/Git changes must block without deleting it.
No existing first-adoption or host-pinned HEAD rejection expectation is relaxed.
"""

import json
from pathlib import Path
from unittest.mock import patch
import unittest

import test_managed_workspace as fixtures
from todo_flow import managed_workspace as managed
from todo_flow import orca_workspace
from todo_flow.adapters import command
from todo_flow.store import Conflict


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()


class HeadObservationTests(unittest.TestCase):
    tearDown = fixtures.ManagedWorkspaceTests.tearDown
    start = fixtures.ManagedWorkspaceTests.start

    def setUp(self):
        fixtures.ManagedWorkspaceTests.setUp(self)
        self.show_calls = 0
        self.heads = []
        self.change_show = lambda result: None
        self.clock = Clock()
        for name, value in (("monotonic", self.clock.monotonic), ("sleep", self.clock.sleep)):
            clock_patch = patch.object(managed, name, value, create=True)
            clock_patch.start()
            self.addCleanup(clock_patch.stop)

    def call(self, cli, args, root):
        result = fixtures.ManagedWorkspaceTests.call(self, cli, args, root)
        if args[:2] == ["worktree", "show"] and args[args.index("--worktree") + 1].startswith(
            "id:"
        ):
            self.show_calls += 1
            if self.heads:
                head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
                worktree = result["result"]["worktree"]
                worktree["head"] = worktree["git"]["head"] = head
            self.change_show(result)
        return result

    def prepare(self):
        self.start()
        self.assertEqual(self.engine.ensure_workspace(self.task), self.candidate)
        self.old_head = command(["git", "rev-parse", "HEAD"], self.candidate)
        command(["git", "commit", "--allow-empty", "-m", "host commit"], self.candidate)
        self.head = command(["git", "rev-parse", "HEAD"], self.candidate)
        self.engine.update(self.task, head=self.head)
        self.show_calls = 0
        self.heads = [self.old_head]
        self.evidence = {
            path: path.read_bytes()
            for path in (self.gate.intent, self.gate.response, managed.receipt_path(self.gate))
        }
        self.data = self.candidate / "user-data.txt"
        self.data.write_text("preserve this untracked file\n")

    def assert_preserved(self):
        self.assertEqual(self.created, 1)
        self.assertTrue(self.candidate.is_dir())
        self.assertEqual(self.data.read_text(), "preserve this untracked file\n")
        self.assertIn(
            str(self.candidate), command(["git", "worktree", "list", "--porcelain"], self.repo)
        )
        self.assertEqual(self.s.track("addition")["head"], self.head)
        for path, content in self.evidence.items():
            self.assertEqual(path.read_bytes(), content)

    def blocked_execution(self, reason, probes):
        with patch("todo_flow.engine.run_worker") as worker:
            self.engine.execute(self.task)
        worker.assert_not_called()
        self.assertEqual(self.show_calls, probes)
        errors = [
            json.loads(row["body"])["error"]
            for row in self.s.snapshot()["events"]
            if row["type"] == "attempt.error"
        ]
        self.assertEqual(len(errors), 1)
        message = errors[0]
        self.assertIn(reason, message)
        # Engine.fail must expose the same cause in the durable decision.
        self.assertIn(message, self.s.snapshot()["decisions"][0]["question"])
        self.assert_preserved()
        return message

    def test_stale_then_current_reuses_checkout_and_launches_once(self):
        self.prepare()
        self.heads = [self.old_head, self.head]
        # The pre-fix code rejects the first old H and never calls run_worker.
        with patch("todo_flow.engine.run_worker", return_value={"summary": "observed"}) as worker:
            self.engine.execute(self.task)
        worker.assert_called_once()
        self.assertEqual(Path(worker.call_args.args[1]["workspace"]), self.candidate)
        self.assertEqual(self.show_calls, 2)
        self.assertEqual(self.clock.sleeps, [1.0])
        self.assertEqual(self.s.snapshot()["decisions"], [])
        self.assert_preserved()

    def test_permanent_stale_is_bounded_and_diagnostics_reach_engine_fail(self):
        self.prepare()
        message = self.blocked_execution("did not converge", 30)
        self.assertEqual(self.clock.now, 30.0)
        self.assertEqual(len(self.clock.sleeps), 30)
        for text in (
            "Orca HEAD has not converged",
            "expected HEAD=" + self.head,
            "Orca HEAD=" + self.old_head,
            "actual Git HEAD=" + self.head,
        ):
            self.assertIn(text, message)

    def test_slow_probe_consumes_the_window_without_another_poll(self):
        self.prepare()
        self.change_show = lambda result: setattr(self.clock, "now", 30.0)
        self.blocked_execution("did not converge", 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_probe_count_is_finite_even_if_clock_does_not_advance(self):
        self.prepare()
        with patch.object(managed, "sleep"):
            self.blocked_execution("did not converge", 31)

    def test_actual_head_change_is_not_retried(self):
        self.prepare()
        command(["git", "commit", "--allow-empty", "-m", "unexpected change"], self.candidate)
        actual = command(["git", "rev-parse", "HEAD"], self.candidate)
        message = self.blocked_execution("Actual Git HEAD mismatch", 1)
        self.assertIn("actual Git HEAD=" + actual, message)
        self.assertEqual(self.clock.sleeps, [])

    def test_actual_head_change_during_wait_blocks_even_if_show_converges(self):
        self.prepare()
        self.heads = [self.old_head, self.head]
        self.clock.on_sleep = lambda: command(
            ["git", "commit", "--allow-empty", "-m", "changed during wait"], self.candidate
        )
        self.blocked_execution("Actual Git HEAD mismatch", 2)

    def test_instance_change_during_retry_is_not_hidden_by_a_current_head(self):
        self.prepare()
        self.heads = [self.old_head, self.head]

        def change(result):
            if self.show_calls == 2:
                result["result"]["worktree"]["instanceId"] = "private-instance-sentinel"

        self.change_show = change
        message = self.blocked_execution("Workspace mismatch: instanceId", 2)
        self.assertNotIn("private-instance-sentinel", message)
        self.assertIn("actual Git HEAD=not-checked", message)

    def test_repository_change_during_retry_blocks(self):
        self.prepare()
        self.clock.on_sleep = lambda: self.repo_response["result"]["repo"].update(id="other")
        self.blocked_execution("Repository identity mismatch", 2)

    def test_common_dir_change_during_retry_blocks(self):
        self.prepare()
        other_common = self.root / "another-common-dir"
        other_common.mkdir()
        original_git = orca_workspace._git

        def git(path, *args):
            # Even matching root/candidate answers must match the durable pin.
            if self.show_calls >= 2 and "--git-common-dir" in args:
                return str(other_common)
            return original_git(path, *args)

        with patch.object(orca_workspace, "_git", git):
            self.blocked_execution("Pinned Git common-dir mismatch", 2)

    def test_claim_loss_during_wait_stops_before_another_observation(self):
        self.prepare()

        def lose_claim():
            with self.s.transaction() as connection:
                connection.execute(
                    "UPDATE tasks SET generation=generation+1 WHERE id=?", (self.task["id"],)
                )

        self.clock.on_sleep = lose_claim
        with (
            patch.object(self.engine, "fail") as failed,
            patch("todo_flow.engine.run_worker") as worker,
        ):
            self.engine.execute(self.task)
        worker.assert_not_called()
        self.assertEqual(self.show_calls, 1)
        self.assertIsInstance(failed.call_args.args[1], Conflict)
        self.assert_preserved()

    def test_changed_ownership_during_wait_stops_before_another_observation(self):
        self.prepare()
        path = managed.receipt_path(self.gate)

        def change_owner():
            value = json.loads(path.read_text())
            value["observation"]["instanceId"] = "replacement"
            path.write_text(json.dumps(value))
            self.evidence[path] = path.read_bytes()

        self.clock.on_sleep = change_owner
        self.blocked_execution("Workspace ownership changed during recovery", 1)

    def test_malformed_observed_head_is_not_retried_or_echoed(self):
        self.prepare()
        self.heads = ["private-response-sentinel"]
        message = self.blocked_execution("Orca HEAD is not an object ID", 1)
        self.assertNotIn("private-response-sentinel", message)
        self.assertIn("Orca HEAD=invalid-or-missing", message)
        self.assertEqual(self.clock.sleeps, [])

    def test_first_adoption_remains_strict_when_only_show_head_is_stale(self):
        self.start()
        self.heads = ["0" * 40]
        with patch("todo_flow.engine.run_worker") as worker:
            self.engine.execute(self.task)
        worker.assert_not_called()
        self.assertEqual(self.show_calls, 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertIsNone(self.s.track("addition")["workspace"])
        self.assertFalse(managed.receipt_path(self.gate).exists())
        self.assertTrue(self.gate.response.exists())
        self.assertTrue(self.candidate.exists())

    def test_foreign_git_repository_is_rejected_even_with_stale_show(self):
        self.prepare()
        foreign = self.root / "foreign"
        command(["git", "clone", "--local", str(self.repo), str(foreign)], self.root)
        original_git = orca_workspace._git

        def git(path, *args):
            if Path(path) == self.candidate and "--git-common-dir" in args:
                return str(foreign / ".git")
            return original_git(path, *args)

        with patch.object(orca_workspace, "_git", git):
            self.blocked_execution("another Git repository (common-dir)", 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_registered_candidate_disagreement_blocks_before_polling(self):
        self.prepare()
        self.engine.update(self.task, branch="todo/another")
        self.blocked_execution("Ownership disagrees with the registered candidate", 0)
        self.assertEqual(self.clock.sleeps, [])
