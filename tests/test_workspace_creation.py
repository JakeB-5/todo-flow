import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from todo_flow.workspace_creation import WorkspaceCreationBlocked, WorkspaceCreationGate


class WorkspaceCreationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.gate = WorkspaceCreationGate(self.directory.name, "example")
        self.task = {
            "id": "work-1",
            "attempt": "attempt-1",
            "track": "example",
            "owner": "driver-1",
            "generation": 1,
            "input_revision": 1,
        }
        # Transport payloads below are synthetic opaque data, not CLI fixtures.
        self.argv = ["orca", "worktree", "create", "--name", "example", "--json"]
        self.claim = Mock()

    def invoke(self, create, *, gate=None, request="request-1"):
        return (gate or self.gate).create_once(
            self.task,
            request=request,
            repo="/repo",
            base="base-commit",
            argv=self.argv,
            assert_claim=self.claim,
            create=create,
        )

    def test_intent_precedes_transport_and_response_is_evidence(self):
        def create(argv):
            saved = json.loads(self.gate.intent.read_text())
            self.assertEqual(saved["claim"], self.task)
            self.assertEqual(saved["request"], "request-1")
            self.assertEqual(saved["base"], "base-commit")
            self.assertEqual(saved["repo"], "/repo")
            self.assertEqual(saved["argv"], argv)
            self.assertFalse(self.gate.response.exists())
            return {"synthetic": "opaque-response"}

        self.assertEqual(self.invoke(create), {"synthetic": "opaque-response"})
        saved = json.loads(self.gate.response.read_text())
        self.assertEqual(saved["response"], {"synthetic": "opaque-response"})
        self.assertEqual(self.claim.call_count, 3)
        transport = Mock()
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        transport.assert_not_called()

    def test_lost_response_blocks_replacement_claim_and_new_request(self):
        transport = Mock(side_effect=TimeoutError("created but response lost"))
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        self.assertEqual(transport.call_count, 1)
        self.assertFalse(self.gate.response.exists())
        self.task.update(id="work-2", attempt="attempt-2", owner="driver-2", generation=2)
        replacement = WorkspaceCreationGate(self.directory.name, "example")
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport, gate=replacement, request="request-2")
        self.assertEqual(transport.call_count, 1)

    def test_partial_intent_blocks_without_parsing_or_overwriting(self):
        self.gate.intent.write_text("{")
        transport = Mock()
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        transport.assert_not_called()
        self.assertEqual(self.gate.intent.read_text(), "{")

    def test_stale_claim_before_intent_has_no_effect(self):
        self.claim.side_effect = RuntimeError("stale claim")
        transport = Mock()
        with self.assertRaisesRegex(RuntimeError, "stale claim"):
            self.invoke(transport)
        self.assertFalse(self.gate.intent.exists())
        transport.assert_not_called()

    def test_claim_lost_during_transport_preserves_response(self):
        self.claim.side_effect = [None, None, RuntimeError("stale claim")]
        with self.assertRaisesRegex(RuntimeError, "stale claim"):
            self.invoke(Mock(return_value={"synthetic": "response"}))
        self.assertTrue(self.gate.response.exists())
        self.claim.side_effect = None
        transport = Mock()
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        transport.assert_not_called()

    def test_failed_intent_fsync_never_calls_transport(self):
        transport = Mock()
        with patch("todo_flow.workspace_creation.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(WorkspaceCreationBlocked):
                self.invoke(transport)
        transport.assert_not_called()
        self.assertTrue(self.gate.intent.exists())

    def test_orphan_response_blocks_before_transport_and_intent_write(self):
        self.gate.response.write_text("prior evidence")
        transport = Mock(return_value={"synthetic": "response"})
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        self.assertEqual(self.gate.response.read_text(), "prior evidence")
        self.assertFalse(self.gate.intent.exists())
        self.task.update(id="work-2", attempt="attempt-2", owner="driver-2", generation=2)
        replacement = WorkspaceCreationGate(self.directory.name, "example")
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport, gate=replacement, request="request-2")
        transport.assert_not_called()

    def test_response_write_failure_cannot_authorize_retry(self):
        def create(argv):
            # A conflicting receipt appears after transport started.
            self.gate.response.write_text("conflicting evidence")
            return {"synthetic": "response"}

        transport = Mock(side_effect=create)
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        self.assertTrue(self.gate.intent.exists())
        self.assertEqual(self.gate.response.read_text(), "conflicting evidence")
        with self.assertRaises(WorkspaceCreationBlocked):
            self.invoke(transport)
        self.assertEqual(transport.call_count, 1)

    def test_clear_check_does_not_create_evidence(self):
        self.gate.require_clear()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_clear_check_rejects_evidence_without_parsing(self):
        for path in (self.gate.intent, self.gate.response):
            for content in ("", "{", '{"version":999}'):
                with self.subTest(path=path.name, content=content):
                    path.write_text(content)
                    with self.assertRaises(WorkspaceCreationBlocked):
                        self.gate.require_clear()
                    self.assertEqual(path.read_text(), content)
                    path.unlink()

    def test_dangling_evidence_symlink_blocks_transport(self):
        for path in (self.gate.intent, self.gate.response):
            with self.subTest(path=path.name):
                path.symlink_to(Path(self.directory.name) / "missing")
                transport = Mock()
                with self.assertRaises(WorkspaceCreationBlocked):
                    self.invoke(transport)
                transport.assert_not_called()
                self.assertTrue(path.is_symlink())
                path.unlink()

    def test_inspection_error_blocks_transport(self):
        transport = Mock()
        with patch.object(Path, "lstat", side_effect=PermissionError("unreadable")):
            with self.assertRaises(WorkspaceCreationBlocked):
                self.invoke(transport)
        transport.assert_not_called()
        self.assertFalse(self.gate.intent.exists())

    def test_tracks_have_separate_intents(self):
        other = WorkspaceCreationGate(self.directory.name, "other")
        self.assertNotEqual(other.intent, self.gate.intent)
        self.assertEqual(self.gate.intent.parent, Path(self.directory.name))
