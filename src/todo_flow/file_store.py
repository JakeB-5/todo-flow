"""Plain files are authoritative. SQLite is a disposable, lock-protected query cache.

A durable redo journal precedes each file commit. All cooperating readers recover it
under the same OS lock before reading. A crash can never publish a partial transition
through the API. grep can observe individual atomic files while a commit is underway.
"""

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

from . import documents
from .release import VERSION, check_catalog, check_config

TABLES = (
    "config",
    "tracks",
    "documents",
    "tasks",
    "attempts",
    "results",
    "waits",
    "decisions",
    "events",
    "effects",
    "watches",
    "locks",
    "findings",
    "triages",
)
JSON_FIELDS = {"body", "document", "verification", "review", "landing", "intent", "receipt"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, dict):
        text = base64.b64decode(text["base64"], validate=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FileDatabase:
    def __init__(self, root, schema):
        self.root, self.schema = Path(root), schema
        self.catalog = self.root / ".catalog.json"
        self.pending = self.root / ".pending.json"
        if (self.root / "state.sqlite").exists() and not self.catalog.exists():
            raise ValueError(
                "Legacy SQLite state: use todo-flow migrate-files --source OLD --target NEW; source is preserved"
            )
        # Reject unsupported state before creating even disposable cache/lock files.
        # Recovery repeats this check while holding the lock.
        self.check_recovery()
        self.cache = self.root / ".cache" / "query.sqlite"
        self.cache.parent.mkdir(exist_ok=True)
        with self.lock():
            self.recover()
            if not self.catalog.exists():
                atomic(
                    self.catalog,
                    dump(
                        {
                            "format": 1,
                            "created_by": VERSION,
                            "min_engine_version": "0.0.1",
                            "version": uuid.uuid4().hex,
                            "records": {},
                        }
                    ),
                )

    @contextlib.contextmanager
    def lock(self):
        with (self.root / ".store.lock").open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def path(self, relative):
        path = self.root / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not path.resolve().is_relative_to(self.root)
        ):
            raise ValueError("Invalid state file path")
        return path

    def check_recovery(self):
        """Read compatibility contracts before applying any recovery write."""
        if self.catalog.exists():
            check_catalog(json.loads(self.catalog.read_text()))
        config_path = self.path("config/1.json")
        if config_path.exists():
            check_config(json.loads(config_path.read_text())["body"])
        if not self.pending.exists():
            return None
        journal = json.loads(self.pending.read_text())
        check_catalog(journal["catalog"])
        for relative, text in journal["writes"].items():
            path = self.path(relative)
            if path.resolve() == config_path.resolve() and text is not None:
                if isinstance(text, dict):
                    text = base64.b64decode(text["base64"], validate=True)
                check_config(json.loads(text)["body"])
        return journal

    def recover(self):
        journal = self.check_recovery()
        if journal is None:
            return
        for relative, text in journal["writes"].items():
            path = self.path(relative)
            if text is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, text)
        # The catalog is the commit marker; delete the journal only after it is durable.
        atomic(self.catalog, dump(journal["catalog"]))
        self.pending.unlink()

    def signature(self, catalog):
        values = [catalog["version"], self.schema]
        for _, record in sorted(catalog["records"].items()):
            for relative in record["files"]:
                stat = self.path(relative).stat()
                values.append((relative, stat.st_mtime_ns, stat.st_size))
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def read(self, entry):
        table, files = entry["table"], entry["files"]
        if table == "documents":
            file = self.path(files[0])
            mode = (
                "html"
                if file.suffix == ".html"
                else "markdown"
                if file.name == "source.md"
                else "record"
            )
            doc = self.read_document(file, mode)
            return {
                "track": entry["key"][0],
                "revision": entry["key"][1],
                "body": json.dumps(doc, ensure_ascii=False, sort_keys=True),
            }
        row = json.loads(self.path(files[0]).read_text())
        if table == "tracks":
            doc_mode = row.pop("document_format", "record")
            document = self.read_document(self.path(files[1]), doc_mode)
            digest = row.pop("document_digest")
            if digest != hashlib.sha256(dump(document).encode()).hexdigest():
                raise ValueError(
                    f"Unregistered edit to {files[1]}; preserve it as a draft, restore the registered file, then register the draft with --expected-revision"
                )
            row["document"] = document
        for key in row.keys() & JSON_FIELDS:
            if row[key] is not None:
                row[key] = json.dumps(row[key], ensure_ascii=False, sort_keys=True)
        return row

    def read_document(self, file, doc_mode):
        doc = documents.decode_source(file.read_text(), doc_mode)
        if doc_mode != "record":
            folder = file.parent / "assets"
            if folder.exists():
                for asset in sorted(folder.rglob("*")):
                    if asset.is_file():
                        doc["presentation"]["assets"].append(
                            {
                                "path": asset.relative_to(folder).as_posix(),
                                "data": base64.b64encode(asset.read_bytes()).decode("ascii"),
                            }
                        )
        return doc

    def document_files(self, folder, doc):
        doc_mode = documents.mode(doc)
        filename = (
            "track.html"
            if doc_mode == "html"
            else "source.md"
            if doc_mode == "markdown"
            else "track.md"
        )
        out = {f"{folder}/{filename}": documents.source(doc)}
        if doc_mode != "html":
            out[f"{folder}/track.html"] = documents.render_html(doc)
        for name, data in documents.assets_for(doc).items():
            out[f"{folder}/assets/{name}"] = {"base64": base64.b64encode(data).decode("ascii")}
        return out

    def encode_row(self, table, row, key):
        row = dict(row)
        for name in row.keys() & JSON_FIELDS:
            if row[name] is not None:
                row[name] = json.loads(row[name])
        if table == "config":
            check_config(row["body"])
        if table == "tracks":
            doc = row.pop("document")
            row["document_digest"] = hashlib.sha256(dump(doc).encode()).hexdigest()
            row["document_format"] = documents.mode(doc)
            return {
                f"tracks/{row['id']}/state.json": dump(row),
                **self.document_files(f"tracks/{row['id']}", doc),
            }
        if table == "documents":
            doc = row["body"]
            if documents.mode(doc) == "record":
                return {
                    f"tracks/{row['track']}/revisions/{row['revision']:06}.md": documents.render(
                        doc
                    )
                }
            return self.document_files(f"tracks/{row['track']}/revisions/{row['revision']:06}", doc)
        directory = "attempt-records" if table == "attempts" else table
        filename = str(key[0]) if len(key) == 1 else hashlib.sha256(dump(key).encode()).hexdigest()
        return {f"{directory}/{filename}.json": dump(row)}

    def prepare(self, catalog, signature):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.cache, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            cached = c.execute("SELECT value FROM _file_meta WHERE id=1").fetchone()
        except sqlite3.DatabaseError:
            cached = None
        if not cached or cached[0] != signature:
            c.close()
            self.cache.unlink(missing_ok=True)
            c = sqlite3.connect(self.cache, timeout=30)
            c.row_factory = sqlite3.Row
            c.executescript(self.schema)
            for entry in catalog["records"].values():
                row = self.read(entry)
                c.execute(
                    f"INSERT INTO {entry['table']} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                    tuple(row.values()),
                )
            c.execute("CREATE TABLE _file_meta(id INTEGER PRIMARY KEY, value TEXT)")
            c.execute("INSERT INTO _file_meta VALUES(1,?)", (signature,))
            c.commit()
        return c

    @contextlib.contextmanager
    def connect(self):
        with self.lock():
            self.recover()
            catalog = json.loads(self.catalog.read_text())
            check_catalog(catalog)
            c = self.prepare(catalog, self.signature(catalog))
            try:
                keys = {}
                c.execute("CREATE TEMP TABLE changes (name TEXT, key TEXT)")
                for table in TABLES:
                    columns = c.execute(f"PRAGMA table_info({table})").fetchall()
                    keys[table] = [
                        r["name"] for r in sorted(columns, key=lambda x: x["pk"]) if r["pk"]
                    ]
                    for operation, source in [
                        ("INSERT", "new"),
                        ("UPDATE", "new"),
                        ("DELETE", "old"),
                    ]:
                        fields = ",".join(f"{source}.{k}" for k in keys[table])
                        c.execute(
                            f"CREATE TEMP TRIGGER change_{table}_{operation} AFTER {operation} ON {table} BEGIN INSERT INTO changes VALUES('{table}',json_array({fields})); END"
                        )
                yield c
                changes = c.execute("SELECT DISTINCT name,key FROM changes").fetchall()
                if changes:
                    writes = {}
                    for table, encoded_key in changes:
                        key = json.loads(encoded_key)
                        id_ = table + ":" + encoded_key
                        previous = catalog["records"].get(id_, {}).get("files", [])
                        where = " AND ".join(f"{k}=?" for k in keys[table])
                        row = c.execute(f"SELECT * FROM {table} WHERE {where}", key).fetchone()
                        if row:
                            encoded = self.encode_row(table, row, key)
                            catalog["records"][id_] = {
                                "table": table,
                                "key": key,
                                "files": list(encoded),
                            }
                            for path, text in encoded.items():
                                value = (
                                    base64.b64decode(text["base64"])
                                    if isinstance(text, dict)
                                    else text.encode()
                                )
                                if (
                                    not self.path(path).exists()
                                    or self.path(path).read_bytes() != value
                                ):
                                    writes[path] = text
                        else:
                            encoded = {}
                            catalog["records"].pop(id_, None)
                        for path in set(previous) - set(encoded):
                            writes[path] = None
                    catalog["version"] = uuid.uuid4().hex
                    atomic(self.pending, dump({"writes": writes, "catalog": catalog}))
                    self.recover()
                    c.execute(
                        "UPDATE _file_meta SET value=? WHERE id=1", (self.signature(catalog),)
                    )
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()
