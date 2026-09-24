import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow import documents
from todo_flow.file_store import FileDatabase
from todo_flow.migrate import migrate
from todo_flow.schema import SCHEMA
from todo_flow.store import Store

DOC = {
    "id": "file-track",
    "title": "검색 가능한 트랙",
    "goal": "폰트 변경 유지",
    "scope": "선택 영역",
    "evidence": "관찰한 현상\n두 번째 줄",
    "conditions": [{"id": "behavior", "text": "폰트가 바뀐다", "method": "재현 테스트"}],
    "area": ["workspace", "font"],
    "trigger": "선행 없음",
    "links": ["prior-track"],
}


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "todo"
        self.s = Store(self.root)
        self.s.configure({"github": None, "base": "main", "endpoint": "review"})
        self.s.register(DOC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_and_execution_survive_deleted_cache(self):
        path = self.root / "tracks/file-track/track.md"
        self.assertEqual(documents.parse(path.read_text()), DOC)
        found = subprocess.run(
            ["rg", "-l", "폰트 변경 유지", str(self.root / "tracks"), "-g", "track.md"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(found.returncode, 0)
        self.s.start(DOC["id"])
        task = self.s.claim("worker")
        self.s.finish(
            task, {"summary": "인계 기록", "next": [{"kind": "work", "purpose": "다음 작업"}]}
        )
        before = self.s.snapshot()
        shutil.rmtree(self.root / ".cache")
        restored = Store(self.root).snapshot()
        self.assertEqual(
            {r["id"]: r for r in before["tasks"]}, {r["id"]: r for r in restored["tasks"]}
        )
        self.assertEqual(before["results"], restored["results"])
        self.assertFalse((self.root / "state.sqlite").exists())
        self.assertIn("인계 기록", next((self.root / "results").glob("*.json")).read_text())

    def test_partial_file_commit_recovers_before_read(self):
        real = FileDatabase.recover
        failed = False

        def crash_once(db):
            nonlocal failed
            if db.pending.exists() and not failed:
                failed = True
                journal = json.loads(db.pending.read_text())
                relative, value = next(iter(journal["writes"].items()))
                from todo_flow.file_store import atomic

                atomic(db.path(relative), value)
                raise OSError("Simulated process death during publication")
            return real(db)

        with patch.object(FileDatabase, "recover", crash_once):
            with self.assertRaises(OSError):
                self.s.start(DOC["id"])
        recovered = Store(self.root)
        self.assertEqual(recovered.track(DOC["id"])["control"], "active")
        self.assertEqual(len(recovered.snapshot()["tasks"]), 1)
        self.assertFalse(recovered.files.pending.exists())
        self.assertTrue(recovered.start(DOC["id"])["existing"])

    def test_concurrent_cli_requests_coalesce_and_no_worker_on_request_only(self):
        args = [
            sys.executable,
            "-m",
            "todo_flow",
            "--state",
            str(self.root),
            "trackrun",
            DOC["id"],
            "--request-only",
        ]
        processes = [
            subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(3)
        ]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn("requestId", out)
        self.assertEqual(len(self.s.snapshot()["tasks"]), 1)
        self.assertEqual(self.s.snapshot()["attempts"], [])

    def test_explicit_legacy_migration_preserves_source(self):
        old = Path(self.tmp.name) / "legacy"
        old.mkdir()
        db = old / "state.sqlite"
        with sqlite3.connect(db) as c:
            c.executescript(SCHEMA)
            c.execute("INSERT INTO config VALUES(1,?)", (json.dumps(self.s.config()),))
            c.execute(
                "INSERT INTO tracks(id,revision,document,updated) VALUES(?,?,?,?)",
                (DOC["id"], 1, json.dumps(DOC), 1),
            )
            c.execute("INSERT INTO documents VALUES(?,?,?)", (DOC["id"], 1, json.dumps(DOC)))
        data = db.read_bytes()
        target = Path(self.tmp.name) / "migrated"
        migrate(old, target)
        self.assertEqual(db.read_bytes(), data)
        self.assertEqual(json.loads(Store(target).track(DOC["id"])["document"]), DOC)
        self.assertTrue((target / "tracks/file-track/track.md").exists())

    def test_unregistered_scope_edit_is_not_silently_executed(self):
        file = self.root / "tracks/file-track/track.md"
        file.write_text(file.read_text().replace("선택 영역", "범위 확대"))
        with self.assertRaisesRegex(ValueError, "Unregistered edit"):
            self.s.start(DOC["id"])
