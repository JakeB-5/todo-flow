"""Synthetic Orca observations with real local Git, executed by the host."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import test_orca_workspace as fixtures
from todo_flow.orca_adoption import (
    capture_creation_pins,
    creation_argv,
    validate_created_workspace,
)
from todo_flow.orca_workspace import show_created_workspace
from todo_flow.workspace_creation import WorkspaceCreationBlocked, WorkspaceCreationGate


class OrcaAdoptionTests(unittest.TestCase):
    setUp = fixtures.OrcaWorkspaceTests.setUp
    git = staticmethod(fixtures.OrcaWorkspaceTests.git)

    def capture(self):
        return capture_creation_pins(
            self.status, self.repo, repo_path=str(self.root), base=self.expected.base
        )

    def fresh(self, branch="todo/fresh", path=None):
        path = path or self.root.parent / "fresh candidate"
        self.git(self.root, "worktree", "add", "-b", branch, str(path), self.expected.base)
        shown = deepcopy(self.show)
        worktree = shown["result"]["worktree"]
        worktree.update(
            id="repo-example::" + str(path), path=str(path), branch="refs/heads/" + branch
        )
        worktree["git"].update(path=str(path), branch="refs/heads/" + branch)
        response = {"ok": True, "result": {"worktree": {"id": worktree["id"]}}}
        return response, shown

    def validate(self, pins, response, shown):
        return validate_created_workspace(self.status, self.repo, response, shown, pins=pins)

    def test_separate_show_and_git_prove_new_checkout_at_pinned_base(self):
        pins = self.capture()
        response, observation = self.fresh(path=self.root.parent / "candidate\nwith newline")
        read = Mock(return_value=observation)
        shown = show_created_workspace(response, read=read)
        evidence = self.validate(pins, response, shown)
        self.assertEqual(evidence["head"], pins["base"])
        self.assertEqual(evidence["common_dir"], pins["common_dir"])
        self.assertEqual(
            evidence["path"], str(Path(observation["result"]["worktree"]["path"]).resolve())
        )
        read.assert_called_once_with(
            [
                "worktree",
                "show",
                "--worktree",
                "id:" + response["result"]["worktree"]["id"],
                "--json",
            ]
        )

    def test_existing_checkout_is_not_new_ownership(self):
        pins = self.capture()
        response = {"ok": True, "result": {"worktree": {"id": self.expected.worktree_id}}}
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(pins, response, self.show)

    def test_preexisting_branch_is_rejected_even_at_a_new_path(self):
        self.git(self.root, "branch", "reserved", self.expected.base)
        pins = self.capture()
        path = self.root.parent / "reserved-candidate"
        self.git(self.root, "worktree", "add", str(path), "reserved")
        shown = deepcopy(self.show)
        worktree = shown["result"]["worktree"]
        worktree.update(
            id="repo-example::" + str(path), path=str(path), branch="refs/heads/reserved"
        )
        worktree["git"].update(path=str(path), branch="refs/heads/reserved")
        response = {"ok": True, "result": {"worktree": {"id": worktree["id"]}}}
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(pins, response, shown)

    def test_existing_path_is_rejected_even_after_branch_replacement(self):
        pins = self.capture()
        self.git(self.workspace, "checkout", "-b", "replacement")
        shown = deepcopy(self.show)
        shown["result"]["worktree"]["branch"] = "refs/heads/replacement"
        shown["result"]["worktree"]["git"]["branch"] = "refs/heads/replacement"
        response = {"ok": True, "result": {"worktree": {"id": self.expected.worktree_id}}}
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(pins, response, shown)

    def test_remote_repo_branch_head_and_nested_mismatches_are_blocked(self):
        pins = self.capture()
        response, original = self.fresh()
        for key, value in (
            ("hostId", "remote"),
            ("repoId", "another-repo"),
            ("branch", "refs/heads/main"),
            ("head", "0" * 40),
            ("isMainWorktree", True),
        ):
            with self.subTest(key=key):
                shown = deepcopy(original)
                shown["result"]["worktree"][key] = value
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate(pins, response, shown)
        self.status["result"]["target"]["kind"] = "remote"
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(pins, response, original)

    def test_actual_commit_cannot_be_hidden_by_unchanged_show(self):
        pins = self.capture()
        response, shown = self.fresh()
        path = Path(shown["result"]["worktree"]["path"])
        self.git(path, "commit", "--allow-empty", "-m", "unexpected advancement")
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(pins, response, shown)

    def test_create_and_show_disagreement_is_rejected(self):
        pins = self.capture()
        response, shown = self.fresh()
        for key, value in (("id", "different-id"), ("branch", "refs/heads/different")):
            with self.subTest(key=key):
                changed = deepcopy(response)
                changed["result"]["worktree"][key] = value
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate(pins, changed, shown)

    def test_unknown_or_incomplete_pins_and_changed_common_dir_are_rejected(self):
        pins = self.capture()
        response, shown = self.fresh()
        for change in ("version", "missing", "common"):
            modified = deepcopy(pins)
            if change == "version":
                modified["version"] = 99
            elif change == "missing":
                del modified["worktrees"]
            else:
                modified["common_dir"] = str(self.root.parent)
            with self.subTest(change=change), self.assertRaises(WorkspaceCreationBlocked):
                self.validate(modified, response, shown)

    def test_remote_or_wrong_repo_and_symbolic_base_cannot_be_pinned(self):
        for change in ("remote", "repo", "base"):
            status, repo = deepcopy(self.status), deepcopy(self.repo)
            base = self.expected.base
            if change == "remote":
                status["result"]["target"]["kind"] = "remote"
            elif change == "repo":
                repo["result"]["repo"]["path"] = str(self.workspace)
            else:
                base = "main"
            with self.subTest(change=change), self.assertRaises(WorkspaceCreationBlocked):
                capture_creation_pins(status, repo, repo_path=str(self.root), base=base)

    def invoke(self, pins, create, claim=None, argv=None):
        self.gate = WorkspaceCreationGate(self.root.parent, "example")
        task = {
            "id": "work-1",
            "attempt": "attempt-1",
            "track": "example",
            "owner": "host-1",
            "generation": 1,
            "input_revision": 1,
        }
        return self.gate.create_once(
            task,
            request="request-1",
            repo=str(self.root.resolve()),
            base=self.expected.base,
            argv=argv or creation_argv("orca", pins, "unique-example"),
            assert_claim=claim or Mock(),
            create=create,
            creation_pins=pins,
        )

    def test_pins_are_durable_before_create_and_response_is_bound_to_intent(self):
        pins = self.capture()
        saved_intent = {}

        def create(argv):
            intent = json.loads(self.gate.intent.read_text())
            saved_intent.update(intent)
            self.assertEqual(intent["version"], 2)
            self.assertEqual(intent["creation_pins"], pins)
            self.assertEqual(intent["argv"], argv)
            self.assertEqual(intent["claim"]["input_revision"], 1)
            response, shown = self.fresh()
            self.validate(intent["creation_pins"], response, shown)
            return response

        response = self.invoke(pins, create)
        saved = json.loads(self.gate.response.read_text())
        digest = hashlib.sha256(
            json.dumps(saved_intent, allow_nan=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.assertEqual(saved["intent_sha256"], digest)
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["response"], response)
        self.assertEqual(saved["claim"], saved_intent["claim"])
        with self.assertRaises(WorkspaceCreationBlocked):
            self.gate.require_clear()

    def test_lost_response_or_replaced_claim_preserves_evidence_and_blocks_retry(self):
        pins = self.capture()

        def create(argv):
            self.fresh()
            raise TimeoutError("response lost after creation")

        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(pins, create)
        intent = self.gate.intent.read_bytes()
        retry = Mock()
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(pins, retry)
        retry.assert_not_called()
        self.assertEqual(self.gate.intent.read_bytes(), intent)

    def test_claim_loss_after_transport_preserves_bound_response(self):
        pins = self.capture()
        claim = Mock(side_effect=[None, None, RuntimeError("claim replaced")])
        with self.assertRaisesRegex(RuntimeError, "claim replaced"):
            self.invoke(pins, lambda argv: self.fresh()[0], claim)
        self.assertTrue(self.gate.response.exists())
        with self.assertRaises(WorkspaceCreationBlocked):
            self.gate.require_clear()

    def test_command_cannot_escape_pinned_repo_base_or_setup_policy(self):
        pins = self.capture()
        original = creation_argv("orca", pins, "unique-example")
        for index, value in ((4, "id:other"), (8, "main"), (10, "run")):
            argv = list(original)
            argv[index] = value
            create = Mock()
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.invoke(pins, create, argv=argv)
            create.assert_not_called()
            self.assertFalse(self.gate.intent.exists())
