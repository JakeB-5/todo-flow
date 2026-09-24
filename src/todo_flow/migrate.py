"""Explicit, non-destructive conversion of legacy SQLite state to canonical files."""

import shutil
import sqlite3
from pathlib import Path

from .file_store import TABLES
from .store import Store


def migrate(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError("Migration target must be a new directory")
    db = source / "state.sqlite"
    if not db.is_file():
        raise ValueError("Legacy state.sqlite not found")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as old:
        old.row_factory = sqlite3.Row
        old.execute("BEGIN")
        if old.execute("SELECT 1 FROM tasks WHERE status='running'").fetchone():
            raise ValueError("Stop and reconcile legacy workers before migrating running claims")
        existing = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = {
            name: [dict(r) for r in old.execute("SELECT * FROM " + name)]
            if name in existing
            else []
            for name in TABLES
        }
    store = Store(target)
    with store.transaction() as c:
        for name, entries in rows.items():
            for row in entries:
                c.execute(
                    f"INSERT INTO {name} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                    tuple(row.values()),
                )
    if (source / "attempts").exists():
        shutil.copytree(source / "attempts", target / "attempts")
    return {"sourcePreserved": str(source), "state": str(target), "tracks": len(rows["tracks"])}
