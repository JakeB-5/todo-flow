"""Exercise workspace creation fencing through the production Engine entry point."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import unittest

import test_flow
from todo_flow.adapters import command, file_lock
from todo_flow.engine import Engine
from todo_flow.store import Conflict
from todo_flow.workspace_creation import WorkspaceCreationBlocked, WorkspaceCreationGate


class EngineWorkspaceCreationTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def start(self):
        self.s.start("addition")
        self.task = self.s.claim("workspace-worker")
        self.engine = Engine(self.s)
        self.gate = WorkspaceCreationGate(self.s.path, "addition")

    def assert_blocked_without_effects(self, task=None, error=WorkspaceCreationBlocked):
        before = self.s.track("addition")
        with (
            patch("todo_flow.engine.command") as external,
            patch("todo_flow.engine.subprocess.run") as subprocess_run,
            patch.object(self.engine, "update", wraps=self.engine.update) as update,
            patch.object(
                self.engine, "_ensure_workspace", wraps=self.engine._ensure_workspace
            ) as route,
            self.assertRaises(error),
        ):
            self.engine.ensure_workspace(task or self.task)
        external.assert_not_called()
        subprocess_run.assert_not_called()
        update.assert_not_called()
        route.assert_not_called()
        self.assertEqual(self.s.track("addition"), before)

    def test_clear_gate_preserves_git_creation_and_existing_checkout(self):
        self.start()
        workspace = self.engine.ensure_workspace(self.task)
        track = self.s.track("addition")
        self.assertTrue(workspace.is_dir())
        self.assertEqual(str(workspace), track["workspace"])
        self.assertEqual(command(["git", "branch", "--show-current"], workspace), track["branch"])
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), track["head"])
        with patch("todo_flow.engine.command", wraps=command) as external:
            self.assertEqual(self.engine.ensure_workspace(self.task), workspace)
        self.assertEqual(
            [call.args[0] for call in external.call_args_list],
            [["git", "branch", "--show-current"], ["git", "rev-parse", "HEAD"]],
        )
        self.assertFalse(self.gate.intent.exists())
        self.assertFalse(self.gate.response.exists())

    def test_evidence_blocks_unregistered_and_existing_checkout_without_changes(self):
        self.start()
        for registered in (False, True):
            self.engine.update(
                self.task,
                branch="main" if registered else None,
                workspace=str(self.repo) if registered else None,
                head=None,
            )
            for name in ("intent", "response"):
                path = getattr(self.gate, name)
                for evidence in ("json", "corrupt", "symlink", "directory"):
                    with self.subTest(registered=registered, name=name, evidence=evidence):
                        if evidence == "symlink":
                            path.symlink_to(self.s.path / "missing-creation-evidence")
                        elif evidence == "directory":
                            path.mkdir()
                        else:
                            path.write_text('{"version": 1}' if evidence == "json" else "{")
                        before = path.lstat()
                        try:
                            self.assert_blocked_without_effects()
                            self.assertEqual(path.lstat(), before)
                            if evidence in ("json", "corrupt"):
                                self.assertEqual(
                                    path.read_text(),
                                    '{"version": 1}' if evidence == "json" else "{",
                                )
                        finally:
                            if evidence == "directory":
                                path.rmdir()
                            else:
                                path.unlink()

    def test_lost_response_and_replacement_owner_cannot_fall_back(self):
        self.start()

        def lose_response(argv):
            raise TimeoutError("Create may have succeeded")

        with self.assertRaises(WorkspaceCreationBlocked):
            self.gate.create_once(
                self.task,
                request="synthetic-create",
                repo=str(self.repo),
                base="a" * 40,
                argv=["synthetic-create"],
                assert_claim=lambda: None,
                create=lose_response,
            )
        intent = self.gate.intent.read_bytes()
        with self.s.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET owner=?,generation=generation+1 WHERE id=?",
                ("replacement", self.task["id"]),
            )
        self.task = {
            **self.task,
            "owner": "replacement",
            "generation": self.task["generation"] + 1,
        }
        self.engine = Engine(self.s)
        self.assert_blocked_without_effects()
        self.assertEqual(self.gate.intent.read_bytes(), intent)
        self.assertFalse(self.gate.response.exists())

    def test_unreadable_evidence_blocks_before_route_selection(self):
        self.start()
        original = Path.lstat

        def deny_evidence(path, *args, **kwargs):
            if path == self.gate.intent:
                raise PermissionError("Cannot inspect creation evidence")
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", deny_evidence):
            self.assert_blocked_without_effects()

    def test_stale_claim_blocks_before_evidence_check_or_effects(self):
        self.start()
        for stale in (
            {**self.task, "owner": "stale-owner"},
            {**self.task, "generation": self.task["generation"] + 1},
            {**self.task, "input_revision": self.task["input_revision"] + 1},
        ):
            with (
                self.subTest(task=stale),
                patch.object(WorkspaceCreationGate, "require_clear") as gate,
            ):
                self.assert_blocked_without_effects(stale, Conflict)
                gate.assert_not_called()

    def test_claim_is_rechecked_after_waiting_for_metadata_lock(self):
        self.start()

        @contextmanager
        def expire_at_lock(path, blocking=False):
            with file_lock(path, blocking=blocking):
                with self.s.transaction() as connection:
                    connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (self.task["id"],))
                yield

        with (
            patch("todo_flow.engine.file_lock", expire_at_lock),
            patch.object(WorkspaceCreationGate, "require_clear") as gate,
        ):
            self.assert_blocked_without_effects(error=Conflict)
            gate.assert_not_called()

    def test_metadata_lock_is_held_during_gate_and_git_route(self):
        self.start()
        lock = self.s.path / "locks" / "git-metadata.lock"
        require_clear = WorkspaceCreationGate.require_clear
        route = self.engine._ensure_workspace
        observed = []

        def check_lock():
            with self.assertRaises(Conflict):
                with file_lock(lock):
                    self.fail("Metadata lock was released")

        def inspect_gate(gate):
            check_lock()
            observed.append("gate")
            return require_clear(gate)

        def inspect_route(task):
            check_lock()
            observed.append("route")
            return route(task)

        with (
            patch.object(WorkspaceCreationGate, "require_clear", inspect_gate),
            patch.object(self.engine, "_ensure_workspace", inspect_route),
        ):
            self.engine.ensure_workspace(self.task)
        self.assertEqual(observed, ["gate", "route"])
