"""Synthetic public show/status fixtures plus host-run local Git validation.

The fixture shape follows host-workspace-responses/{worktree-show,repo-show,status}
captured on 2026-09-27. All identifiers, paths and revisions are synthetic. The
candidate flags deliberately describe a linked worktree instead of the captured
main checkout. SHOW is never presented as a captured create response.
"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from todo_flow.orca_workspace import (
    WorkspaceExpectation,
    show_created_workspace,
    validate_workspace,
)
from todo_flow.workspace_creation import WorkspaceCreationBlocked


STATUS = {
    "id": "synthetic-status",
    "ok": True,
    "result": {
        "target": {"kind": "local"},
        "app": {"running": True, "pid": 123, "desktopWindowStatus": "available"},
        "runtime": {
            "state": "ready",
            "reachable": True,
            "connectionState": "connected",
            "runtimeId": "synthetic-runtime",
            "appVersion": "1.4.202",
            "capabilities": [],
        },
        "graph": {"state": "ready"},
    },
}
REPO = {
    "id": "synthetic-repo-request",
    "ok": True,
    "result": {"repo": {"id": "repo-example", "path": "/synthetic/repo", "kind": "git"}},
}
SHOW = {
    "id": "synthetic-show-request",
    "ok": True,
    "result": {
        "worktree": {
            "id": "repo-example::/synthetic/candidate",
            "identity": {
                "key": "wt2:local:instance-example",
                "executionHostId": "local",
                "instanceId": "instance-example",
            },
            "instanceId": "instance-example",
            "repoId": "repo-example",
            "projectId": "github:example/project",
            "hostId": "local",
            "projectHostSetupId": "setup-example",
            "path": "/synthetic/candidate",
            "head": "a" * 40,
            "branch": "refs/heads/todo/example",
            "isBare": False,
            "isMainWorktree": False,
            "git": {
                "path": "/synthetic/candidate",
                "head": "a" * 40,
                "branch": "refs/heads/todo/example",
                "isBare": False,
                "isMainWorktree": False,
            },
        }
    },
}


class OrcaWorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "repo"
        self.root.mkdir()
        self.workspace = Path(directory.name) / "candidate"
        self.git(self.root, "init", "-b", "main")
        self.git(self.root, "config", "user.name", "Synthetic Test")
        self.git(self.root, "config", "user.email", "synthetic@example.invalid")
        self.git(self.root, "commit", "--allow-empty", "-m", "base")
        base = self.git(self.root, "rev-parse", "HEAD")
        self.git(self.root, "worktree", "add", "-b", "todo/example", str(self.workspace), base)
        self.status, self.repo, self.show = deepcopy(STATUS), deepcopy(REPO), deepcopy(SHOW)
        self.repo["result"]["repo"]["path"] = str(self.root)
        worktree = self.show["result"]["worktree"]
        worktree.update(
            id="repo-example::" + str(self.workspace), path=str(self.workspace), head=base
        )
        worktree["git"].update(path=str(self.workspace), head=base)
        self.expected = WorkspaceExpectation(
            worktree_id=worktree["id"],
            identity_key="wt2:local:instance-example",
            instance_id="instance-example",
            repo_id="repo-example",
            project_id="github:example/project",
            setup_id="setup-example",
            repo_path=str(self.root),
            path=str(self.workspace),
            branch="todo/example",
            base=base,
            head=base,
        )

    @staticmethod
    def git(path, *args):
        return subprocess.run(
            ["git", *args], cwd=path, capture_output=True, text=True, check=True
        ).stdout.strip()

    def validate(self, **kwargs):
        return validate_workspace(
            self.status, self.repo, self.show, expected=kwargs.get("expected", self.expected)
        )

    def test_local_linked_checkout_matches_observation(self):
        evidence = self.validate()
        self.assertEqual(evidence["head"], self.expected.base)
        self.assertEqual(evidence["path"], str(self.workspace.resolve()))
        self.assertEqual(evidence["common_dir"], str((self.root / ".git").resolve()))

    def test_each_identity_and_nested_git_field_is_checked(self):
        for key in (
            "id",
            "instanceId",
            "repoId",
            "projectId",
            "projectHostSetupId",
            "hostId",
            "path",
            "head",
            "branch",
            "isBare",
            "isMainWorktree",
        ):
            with self.subTest(key=key):
                original = deepcopy(self.show)
                self.show["result"]["worktree"][key] = "wrong"
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate()
                self.show = original
        for key in ("key", "instanceId", "executionHostId"):
            with self.subTest(identity=key):
                original = deepcopy(self.show)
                self.show["result"]["worktree"]["identity"][key] = "remote"
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate()
                self.show = original
        for key in tuple(self.show["result"]["worktree"]["git"]):
            with self.subTest(git=key):
                original = deepcopy(self.show)
                del self.show["result"]["worktree"]["git"][key]
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate()
                self.show = original

    def test_remote_or_unreachable_runtime_is_rejected(self):
        for change in ("remote", "unreachable", "not-ready"):
            with self.subTest(change=change):
                self.status = deepcopy(STATUS)
                if change == "remote":
                    self.status["result"]["target"]["kind"] = "remote"
                elif change == "unreachable":
                    self.status["result"]["runtime"]["reachable"] = False
                else:
                    self.status["result"]["runtime"]["state"] = "starting"
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate()

    def test_repository_identity_and_path_are_checked(self):
        for key, value in (("id", "other"), ("kind", "folder"), ("path", str(self.workspace))):
            with self.subTest(key=key):
                original = deepcopy(self.repo)
                self.repo["result"]["repo"][key] = value
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.validate()
                self.repo = original

    def test_actual_repository_and_branch_cannot_be_spoofed_by_show(self):
        other = self.root.parent / "other"
        self.git(self.root.parent, "clone", "--local", str(self.root), str(other))
        expected = replace(self.expected, repo_path=str(other))
        self.repo["result"]["repo"]["path"] = str(other)
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate(expected=expected)
        self.repo["result"]["repo"]["path"] = str(self.root)
        self.git(self.workspace, "checkout", "-b", "another")
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate()

    def test_later_owned_head_is_distinct_from_creation_base(self):
        self.git(self.workspace, "commit", "--allow-empty", "-m", "candidate")
        head = self.git(self.workspace, "rev-parse", "HEAD")
        # Stale show cannot hide an actual changed HEAD.
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate()
        self.show["result"]["worktree"]["head"] = head
        self.show["result"]["worktree"]["git"]["head"] = head
        # First registration still requires the original base as expected HEAD.
        with self.assertRaises(WorkspaceCreationBlocked):
            self.validate()
        evidence = self.validate(expected=replace(self.expected, head=head))
        self.assertEqual(evidence["head"], head)
        self.assertEqual(evidence["base"], self.expected.base)

    def test_unrelated_or_symbolic_base_is_rejected(self):
        self.git(self.root, "checkout", "--orphan", "unrelated")
        self.git(self.root, "commit", "--allow-empty", "-m", "unrelated root")
        unrelated = self.git(self.root, "rev-parse", "HEAD")
        for base in ("main", unrelated, "0" * 40):
            with self.subTest(base=base), self.assertRaises(WorkspaceCreationBlocked):
                self.validate(expected=replace(self.expected, base=base))

    def test_create_identifier_drives_a_separate_show_read(self):
        # Minimal synthetic contract, not a captured successful create response.
        created = {"ok": True, "result": {"worktree": {"id": self.expected.worktree_id}}}
        read = Mock(return_value=self.show)
        shown = show_created_workspace(created, read=read)
        self.assertEqual(shown, self.show)
        read.assert_called_once_with(
            ["worktree", "show", "--worktree", "id:" + self.expected.worktree_id, "--json"]
        )
        for response in ({}, {"ok": False}, {"ok": True, "result": {}}):
            read.reset_mock()
            with self.assertRaises(WorkspaceCreationBlocked):
                show_created_workspace(response, read=read)
            read.assert_not_called()
        read.side_effect = TimeoutError("Orca unavailable")
        with self.assertRaises(WorkspaceCreationBlocked):
            show_created_workspace(created, read=read)
        read.assert_called_once()

    def test_show_cannot_substitute_another_created_resource(self):
        created = {"ok": True, "result": {"worktree": {"id": "another-id"}}}
        with self.assertRaises(WorkspaceCreationBlocked):
            show_created_workspace(created, read=Mock(return_value=self.show))


if __name__ == "__main__":
    unittest.main()
