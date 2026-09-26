"""Launcher display must not turn route selection into execution evidence."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.cli import dispatch, parser
from todo_flow.launch_display import describe_launch, read_launch, task_launch
from todo_flow.store import Store


class LaunchDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.configure({"language": "ko"})
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(id,track,kind,purpose,created,updated) VALUES(?,?,?,?,?,?)",
                ("task-one", "track-one", "work", "Fixture", 1, 1),
            )
            connection.execute(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,?)",
                ("attempt-one", "task-one", 1, "running"),
            )
        self.folder = self.store.path / "attempts/attempt-one"
        self.folder.mkdir(parents=True, exist_ok=True)

    def write(self, record):
        (self.folder / "launch.json").write_text(json.dumps(record), encoding="utf-8")

    def test_cli_project_language_and_override_preserve_raw_evidence(self):
        self.write(
            {
                "backend": "headless",
                "status": "selected",
                "selection": {
                    "requested": "auto",
                    "backend": "headless",
                    "reason": "cli_missing",
                    "native_ready": False,
                },
            }
        )
        before = (self.folder / "launch.json").read_bytes()
        for flags, expected in (
            ([], "Orca CLI를 찾을 수 없음"),
            (["--language", "en"], "Orca CLI not found"),
        ):
            with self.subTest(flags=flags):
                output = io.StringIO()
                args = parser().parse_args(["launch-status", "task-one", *flags])
                with patch("todo_flow.cli.Store", return_value=self.store), redirect_stdout(output):
                    dispatch(args)
                result = json.loads(output.getvalue())
                self.assertEqual(result["summary"]["reason"], expected)
                self.assertEqual(result["record"]["selection"]["reason"], "cli_missing")
                self.assertEqual(result["record"]["status"], "selected")
        self.assertEqual((self.folder / "launch.json").read_bytes(), before)

    def test_failure_before_launch_does_not_claim_a_backend(self):
        self.write(
            {
                "backend": None,
                "status": "unavailable",
                "selection": {"requested": "orca", "reason": "remote_host_mismatch"},
            }
        )
        result = task_launch(self.store, "task-one")
        self.assertEqual(result["summary"]["backend"], "선택된 backend 없음")
        self.assertEqual(result["summary"]["status"], "실행 전 선택 실패")
        self.assertEqual(result["summary"]["reason"], "Orca host가 로컬이 아님")

    def test_headless_and_terminal_acceptance_are_not_worker_start(self):
        explicit = describe_launch(
            {
                "backend": "headless",
                "status": "selected",
                "selection": {"reason": "explicit_headless"},
            }
        )
        self.assertEqual(explicit["reason"], "Headless explicitly requested")
        self.assertIn("not established", explicit["status"])
        orca = describe_launch(
            {
                "backend": "orca",
                "status": "accepted",
                "selection": {"reason": "native_contract_unverified"},
            }
        )
        self.assertEqual(orca["backend"], "Orca terminal (command worker)")
        self.assertIn("worker start not established", orca["status"])
        self.assertIn("not implemented", orca["native"])
        self.assertIn("have not been performed", orca["validation"])

    def test_missing_and_partial_receipts_remain_unknown(self):
        self.assertEqual(task_launch(self.store, "task-one")["evidence"], "missing")
        for text in ("{", "[]", '{"selection": []}', '{"backend": []}', " " * 65537):
            with self.subTest(text=text[:30]):
                (self.folder / "launch.json").write_text(text, encoding="utf-8")
                result = task_launch(self.store, "task-one")
                self.assertEqual(result["evidence"], "unreadable")
                self.assertIsNone(result["summary"])

    def test_latest_attempt_without_receipt_does_not_show_old_success(self):
        self.write({"backend": "orca", "status": "accepted"})
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,?)",
                ("attempt-two", "task-one", 2, "running"),
            )
        result = task_launch(self.store, "task-one")
        self.assertEqual(result["attempt"], "attempt-two")
        self.assertEqual(result["evidence"], "missing")
        self.assertIsNone(result["record"])

    def test_legacy_unknown_values_and_display_allowlist(self):
        self.write({"backend": "tmux", "status": "accepted", "argv": ["PRIVATE_ARG"]})
        result = task_launch(self.store, "task-one", "en")
        self.assertEqual(result["summary"]["reason"], "Not recorded")
        self.assertNotIn("PRIVATE_ARG", json.dumps(result))
        summary = describe_launch(
            {"backend": "future", "status": "future-status", "selection": {"reason": "future"}}
        )
        self.assertEqual(summary["backend"], "future")
        self.assertEqual(summary["reason"], "future")
        self.assertEqual(summary["status"], "future-status")

    def test_unknown_task_and_unsafe_attempt_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown task"):
            task_launch(self.store, "missing-task")
        with self.assertRaisesRegex(ValueError, "Invalid attempt"):
            read_launch(self.store.path, "../outside")
