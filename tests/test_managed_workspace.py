"""Real Engine calls and local Git; Orca public responses are synthetic."""

from copy import deepcopy
import json
from unittest.mock import patch
import unittest

import test_flow
from test_orca_workspace import STATUS, REPO, SHOW
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow import managed_workspace as managed
from todo_flow.store import Conflict
from todo_flow.workspace_creation import WorkspaceCreationBlocked, WorkspaceCreationGate


class ManagedWorkspaceTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def start(self):
        self.s.start("addition")
        self.task = self.s.claim("synthetic-owner")
        self.engine = Engine(self.s)
        self.engine.config = {
            **self.engine.config,
            "worker": {"type": "codex"},
            "worker_launcher": "orca",
        }
        self.created = 0
        self.candidate = (self.repo.parent / "managed candidate").resolve()
        self.shown = None
        self.gate = WorkspaceCreationGate(self.s.path, "addition")
        self.repo_response = deepcopy(REPO)
        self.repo_response["result"]["repo"]["path"] = str(self.repo)
        self.cli_patch = patch.object(managed, "_call", self.call)
        self.route_patch = patch.object(
            managed,
            "select_launcher",
            return_value={
                "backend": "orca",
                "cli": "synthetic-orca",
                "selection": {"orca": {"advertised": {"managed_agent_worktree": True}}},
            },
        )
        self.cli_patch.start()
        self.route_patch.start()
        self.addCleanup(self.cli_patch.stop)
        self.addCleanup(self.route_patch.stop)

    def call(self, cli, args, root):
        if args[0] == "status":
            return deepcopy(STATUS)
        if args[0] == "repo":
            return deepcopy(self.repo_response)
        if args[:2] == ["worktree", "create"]:
            self.assertTrue(self.gate.intent.exists())
            self.created += 1
            self.assertEqual(self.created, 1)
            base = args[args.index("--base-branch") + 1]
            command(
                ["git", "worktree", "add", "-b", "todo/managed", str(self.candidate), base],
                self.repo,
            )
            self.shown = deepcopy(SHOW)
            wt = self.shown["result"]["worktree"]
            wt.update(
                id="repo-example::" + str(self.candidate),
                path=str(self.candidate),
                branch="refs/heads/todo/managed",
                head=base,
            )
            wt["git"].update(path=str(self.candidate), branch=wt["branch"], head=base)
            return {"ok": True, "result": {"worktree": {"id": wt["id"]}}}
        if args[args.index("--worktree") + 1].startswith("path:"):
            return {"ok": True, "result": {"worktree": {"repoId": "repo-example"}}}
        result = deepcopy(self.shown)
        head = command(["git", "rev-parse", "HEAD"], self.candidate)
        result["result"]["worktree"]["head"] = head
        result["result"]["worktree"]["git"]["head"] = head
        return result

    def test_create_reuse_and_later_commit_preserve_owned_candidate(self):
        self.start()
        self.assertEqual(self.engine.ensure_workspace(self.task), self.candidate)
        self.assertEqual(self.s.track("addition")["branch"], "todo/managed")
        command(["git", "commit", "--allow-empty", "-m", "later"], self.candidate)
        head = command(["git", "rev-parse", "HEAD"], self.candidate)
        self.engine.update(self.task, head=head)
        self.assertEqual(self.engine.ensure_workspace(self.task), self.candidate)
        self.assertEqual(self.s.track("addition")["head"], head)
        self.assertEqual(self.created, 1)

    def test_receipt_recovers_interruption_before_registration(self):
        self.start()
        with patch.object(managed, "_register", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.engine.ensure_workspace(self.task)
        self.assertIsNone(self.s.track("addition")["workspace"])
        self.assertTrue(managed.receipt_path(self.gate).exists())
        self.assertEqual(self.engine.ensure_workspace(self.task), self.candidate)
        self.assertEqual(self.created, 1)

    def test_lost_create_response_blocks_without_git_fallback(self):
        self.start()
        original = self.call

        def lose(cli, args, root):
            result = original(cli, args, root)
            if args[:2] == ["worktree", "create"]:
                raise TimeoutError("lost response")
            return result

        with patch.object(managed, "_call", lose), self.assertRaises(WorkspaceCreationBlocked):
            self.engine.ensure_workspace(self.task)
        with (
            patch.object(self.engine, "_ensure_workspace") as fallback,
            self.assertRaises(WorkspaceCreationBlocked),
        ):
            self.engine.ensure_workspace(self.task)
        fallback.assert_not_called()
        self.assertEqual(self.created, 1)

    def test_changed_claim_after_create_preserves_response_without_ownership(self):
        self.start()
        original = self.call

        def replace(cli, args, root):
            result = original(cli, args, root)
            if args[:2] == ["worktree", "create"]:
                with self.s.transaction() as connection:
                    connection.execute(
                        "UPDATE tasks SET generation=generation+1 WHERE id=?", (self.task["id"],)
                    )
            return result

        with patch.object(managed, "_call", replace), self.assertRaises(Conflict):
            self.engine.ensure_workspace(self.task)
        self.assertTrue(self.gate.response.exists())
        self.assertFalse(managed.receipt_path(self.gate).exists())
        self.assertIsNone(self.s.track("addition")["workspace"])

    def test_corrupt_receipt_or_changed_actual_branch_blocks_recovery(self):
        self.start()
        self.engine.ensure_workspace(self.task)
        receipt = managed.receipt_path(self.gate)
        original = receipt.read_bytes()
        for edit in ("unknown-version", "wrong-digest", "symlink"):
            receipt.unlink()
            if edit == "symlink":
                receipt.symlink_to(receipt.parent / "absent")
            else:
                body = json.loads(original)
                body["version" if edit == "unknown-version" else "intent_sha256"] = 99
                receipt.write_text(json.dumps(body))
            with self.subTest(edit=edit), self.assertRaises(WorkspaceCreationBlocked):
                self.engine.ensure_workspace(self.task)
        receipt.unlink()
        receipt.write_bytes(original)
        command(["git", "checkout", "-b", "other"], self.candidate)
        with self.assertRaises(WorkspaceCreationBlocked):
            self.engine.ensure_workspace(self.task)
        self.assertEqual(self.created, 1)

    def test_remote_or_mismatched_repo_show_never_registers(self):
        for key, value in (
            ("hostId", "remote"),
            ("repoId", "wrong"),
            ("branch", "refs/heads/wrong"),
            ("head", "0" * 40),
        ):
            with self.subTest(key=key):
                # Each subcase has isolated repository/state and independent intent.
                case = ManagedWorkspaceTests()
                case.setUp()
                try:
                    case.start()
                    original = case.call

                    def mismatch(cli, args, root):
                        result = original(cli, args, root)
                        if args[:2] == ["worktree", "show"] and args[
                            args.index("--worktree") + 1
                        ].startswith("id:"):
                            result["result"]["worktree"][key] = value
                        return result

                    with (
                        patch.object(managed, "_call", mismatch),
                        self.assertRaises(WorkspaceCreationBlocked),
                    ):
                        case.engine.ensure_workspace(case.task)
                    self.assertIsNone(case.s.track("addition")["workspace"])
                    self.assertFalse(managed.receipt_path(case.gate).exists())
                finally:
                    case.doCleanups()
                    case.tearDown()
