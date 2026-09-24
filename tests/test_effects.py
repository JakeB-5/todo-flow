import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from todo_flow.adapters import GitHub
from todo_flow.store import Store


class IssueEffectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.configure({"github": "owner/repo", "repo": self.tmp.name})
        self.store.register(
            {
                "id": "track",
                "title": "test",
                "goal": "goal",
                "scope": "scope",
                "evidence": "request",
                "conditions": [{"id": "c", "text": "done", "method": "test"}],
            }
        )
        self.store.start("track")
        self.task = self.store.claim("worker")
        self.github = GitHub(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_already_closed_issue_does_not_write_again(self):
        self.github.api = Mock(
            return_value={"state": "closed", "html_url": "https://example.invalid/issue"}
        )
        receipt = self.github.close_issue(self.task, 1)
        self.assertEqual(receipt["state"], "closed")
        self.github.api.assert_called_once_with("issues/1")
        self.github.close_issue(self.task, 1)
        self.github.api.assert_called_once()

    def test_concurrent_auto_close_recovers_after_patch_error(self):
        self.github.api = Mock(
            side_effect=[
                {"state": "open"},
                RuntimeError("422"),
                {"state": "closed", "html_url": "https://example.invalid/issue"},
            ]
        )
        receipt = self.github.close_issue(self.task, 1)
        self.assertEqual(receipt["state"], "closed")

    def test_failure_is_not_claimed_closed(self):
        self.github.api = Mock(
            side_effect=[{"state": "open"}, RuntimeError("422"), {"state": "open"}]
        )
        with self.assertRaises(RuntimeError):
            self.github.close_issue(self.task, 1)
        self.assertIsNone(self.store.snapshot()["effects"][0]["receipt"])

    def test_repair_reopens_issue_and_old_close_receipt_cannot_skip_new_close(self):
        self.github.api = Mock(
            return_value={"state": "closed", "html_url": "https://example.invalid/issue"}
        )
        self.github.close_issue(self.task, 1)
        with self.store.transaction() as c:
            c.execute("UPDATE tracks SET branch='repair-cycle',head='new-head' WHERE id='track'")
        self.github.api = Mock(
            side_effect=[
                {"state": "closed"},
                {"state": "open", "html_url": "https://example.invalid/issue"},
            ]
        )
        self.assertEqual(self.github.reopen_issue(self.task, 1)["state"], "open")
        self.github.reopen_issue(self.task, 1)
        self.assertEqual(self.github.api.call_count, 2)
        self.github.api = Mock(
            side_effect=[
                {"state": "open"},
                {"state": "closed", "html_url": "https://example.invalid/issue"},
            ]
        )
        self.assertEqual(self.github.close_issue(self.task, 1)["state"], "closed")
        self.assertEqual(self.github.api.call_count, 2)
