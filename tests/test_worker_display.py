import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow.projections import Dashboard
from todo_flow.store import Store, encode
from todo_flow.worker_display import LIMIT, read_worker


class WorkerDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.configure(
            {
                "github": None,
                "base": "main",
                "endpoint": "review",
                "worker": {"type": "codex", "model": "current-config", "effort": "low"},
            }
        )
        for name in ("alpha", "beta"):
            self.store.register(
                {
                    "id": name,
                    "title": name,
                    "goal": "Synthetic worker display",
                    "scope": "Dashboard",
                    "evidence": "Synthetic fixture",
                    "conditions": [{"id": "ok", "text": "Visible", "method": "Test"}],
                    "estimate": "not-reasoning-effort",
                }
            )
        with self.store.transaction() as c:
            c.executemany(
                "INSERT INTO tasks(id,track,kind,purpose,status,lease,created,updated)"
                " VALUES(?,?,'work','Synthetic instructions','running',3000000000,1,1)",
                [("one", "alpha"), ("two", "beta")],
            )
            c.executemany(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,'running')",
                [("a-old", "one", 1), ("a-new", "one", 2), ("b-one", "two", 1)],
            )
        self.write("a-old", "worker-selection.json", {"selected": {"model": "old-model"}})
        self.write(
            "a-new",
            "worker-selection.json",
            {
                "selected": {"provider": "codex", "model": "chosen-a", "effort": "high"},
                "provider_confirmed": None,
                "command": "PRIVATE_COMMAND",
            },
        )
        self.write(
            "a-new",
            "native-session.json",
            {
                "selected": {"model": "chosen-a", "effort": "high"},
                "provider_confirmed": {"model": "actual-a", "effort": None},
                "transcript": "PRIVATE_TRANSCRIPT",
            },
        )
        self.write(
            "b-one",
            "worker-selection.json",
            {"selected": {"provider": "claude", "model": "chosen-b", "effort": "low"}},
        )
        self.dashboard = Dashboard(self.store)

    def write(self, attempt, filename, value):
        folder = self.store.path / "attempts" / attempt
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(encode(value), encoding="utf-8")

    def snapshot(self):
        with self.store.connect() as c:
            rows = {
                table: [tuple(row) for row in c.execute("SELECT * FROM " + table)]
                for table in ("tracks", "tasks", "attempts", "events", "config")
            }
        files = {
            str(path): path.read_bytes()
            for path in self.store.path.rglob("*.json")
            if path.is_file()
        }
        return rows, files

    def test_bounded_current_and_detail_share_attempt_without_mutation(self):
        before = self.snapshot()
        with patch("todo_flow.projections.read_worker", wraps=read_worker) as reader:
            first = self.dashboard.activity(limit=1)
            reader.assert_called_once_with(self.store.path, "a-new")
        self.assertEqual(first["total"], 2)
        self.assertTrue(first["hasMore"])
        current = first["items"][0]["current"]
        detail = self.dashboard.task("one")
        self.assertEqual(current["worker"], detail["worker"])
        self.assertEqual(detail["attempt"]["id"], detail["worker"]["attempt"])
        worker = current["worker"]
        self.assertEqual(worker["model"]["selected"]["value"], "chosen-a")
        self.assertEqual(worker["model"]["confirmed"]["value"], "actual-a")
        self.assertEqual(worker["model"]["confirmed"]["status"], "confirmed")
        self.assertEqual(
            worker["model"]["confirmed"]["source"],
            "native-session.json:provider_confirmed.model",
        )
        self.assertEqual(worker["effort"]["selected"]["value"], "high")
        self.assertEqual(worker["effort"]["confirmed"]["status"], "unconfirmed")
        second = self.dashboard.activity(limit=1, offset=1)
        other = second["items"][0]["current"]["worker"]
        self.assertEqual(other, self.dashboard.task("two")["worker"])
        self.assertEqual(other["model"]["selected"]["value"], "chosen-b")
        self.assertEqual(other["effort"]["selected"]["value"], "low")
        self.assertEqual(other["model"]["confirmed"]["status"], "unconfirmed")
        body = encode([first, second, detail])
        for private in ("PRIVATE_", "old-model", "current-config", "not-reasoning-effort"):
            self.assertNotIn(private, body)
        self.assertEqual(before, self.snapshot())

    def test_retry_missing_record_never_borrows_previous_attempt(self):
        with self.store.transaction() as c:
            c.execute(
                "INSERT INTO attempts(id,task,started,status)"
                " VALUES('a-retry','one',3,'running')"
            )
        detail = self.dashboard.task("one")
        worker = detail["worker"]
        self.assertEqual(worker["attempt"], "a-retry")
        self.assertEqual(worker["model"]["selected"]["status"], "missing")
        self.assertEqual(worker["effort"]["selected"]["status"], "missing")
        self.assertEqual(self.dashboard.activity()["items"][0]["current"]["worker"], worker)
        self.write(
            "a-retry",
            "worker-selection.json",
            {"selected": {"provider": "codex", "model": "retry-model", "effort": None}},
        )
        updated = self.dashboard.task("one")["worker"]
        self.assertEqual(updated["model"]["selected"]["value"], "retry-model")
        self.assertEqual(updated["effort"]["selected"]["status"], "delegated")
        self.assertEqual(updated["model"]["confirmed"]["status"], "unconfirmed")
        self.assertNotIn("actual-a", encode(updated))

    def test_partial_fields_defaults_and_legacy_absence_are_distinct(self):
        self.write(
            "partial",
            "worker-selection.json",
            {
                "selected": {"provider": "codex", "model": None},
                "provider_confirmed": {"effort": "medium"},
            },
        )
        worker = read_worker(self.store.path, "partial")
        self.assertEqual(worker["model"]["selected"]["status"], "delegated")
        self.assertEqual(worker["effort"]["selected"]["status"], "missing")
        self.assertEqual(worker["model"]["confirmed"]["status"], "unconfirmed")
        self.assertEqual(worker["effort"]["confirmed"]["value"], "medium")
        for attempt in (None, "legacy"):
            missing = read_worker(self.store.path, attempt)
            for key in ("model", "effort"):
                for section in ("selected", "confirmed"):
                    self.assertEqual(missing[key][section]["status"], "missing")
                    self.assertIsNone(missing[key][section]["value"])

    def test_unreadable_and_oversized_records_do_not_leak_or_fall_back(self):
        folder = self.store.path / "attempts" / "broken"
        folder.mkdir()
        path = folder / "worker-selection.json"
        for raw in (b"{", b"[]", b"\xff", b" " * (LIMIT + 1)):
            with self.subTest(raw_length=len(raw)):
                path.write_bytes(raw)
                worker = read_worker(self.store.path, "broken")
                self.assertEqual(worker["evidence"][path.name], "unreadable")
                self.assertEqual(worker["model"]["selected"]["status"], "unreadable")
                self.assertIsNone(worker["model"]["selected"]["value"])
                self.assertNotIn("current-config", encode(worker))
        self.write("broken", path.name, {"selected": {"model": "x" * 513, "effort": 7}})
        worker = read_worker(self.store.path, "broken")
        self.assertEqual(worker["model"]["selected"]["status"], "unreadable")
        self.assertEqual(worker["effort"]["selected"]["status"], "unreadable")
        self.assertNotIn("x" * 513, encode(worker))
