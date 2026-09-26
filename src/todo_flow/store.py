"""Filesystem authority for track documents and durable execution records."""

import contextlib
import hashlib
import json
import time
import uuid
from pathlib import Path


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex[:16]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Conflict(RuntimeError):
    pass


class Store:
    def __init__(self, state):
        self.path = Path(state).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        from .file_store import FileDatabase
        from .schema import SCHEMA

        self.files = FileDatabase(self.path, SCHEMA)

    @contextlib.contextmanager
    def connect(self):
        with self.files.connect() as c:
            yield c

    @contextlib.contextmanager
    def transaction(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            yield c

    def config(self):
        with self.connect() as c:
            row = c.execute("SELECT body FROM config WHERE id=1").fetchone()
        if not row:
            raise ValueError("Project not initialized")
        from .release import check_config

        config = json.loads(row[0])
        check_config(config)
        return config

    def configure(self, value):
        from .release import check_config

        check_config(value)
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM config").fetchone():
                raise Conflict(
                    "Project already initialized; configuration is immutable for this run"
                )
            c.execute("INSERT INTO config VALUES(1,?)", (encode(value),))

    def event(self, c, kind, track, value):
        c.execute(
            "INSERT INTO events(type,track,body,at) VALUES(?,?,?,?)",
            (kind, track, encode(value), time.time()),
        )

    def track(self, track_id, c=None):
        if c is None:
            with self.connect() as conn:
                return self.track(track_id, conn)
        row = c.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if not row:
            raise ValueError("Unknown track: " + track_id)
        return dict(row)

    def register(self, doc, expected=None, connection=None):
        import re

        from .documents import validate_presentation

        validate_presentation(doc)
        required = ("id", "title", "goal", "scope", "evidence", "conditions")
        if any(not doc.get(k) for k in required):
            raise ValueError("Document requires: " + ", ".join(required))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", doc["id"]):
            raise ValueError("Invalid track ID")
        conditions = doc["conditions"]
        if not isinstance(conditions, list) or any(
            not isinstance(x, dict) or not all(x.get(k) for k in ("id", "text", "method"))
            for x in conditions
        ):
            raise ValueError("Each condition requires id, text, method")
        if len({x["id"] for x in conditions}) != len(conditions):
            raise ValueError("Duplicate condition IDs")
        with (
            contextlib.nullcontext(connection)
            if connection is not None
            else self.transaction() as c
        ):
            row = c.execute("SELECT * FROM tracks WHERE id=?", (doc["id"],)).fetchone()
            if row and row["document"] == encode(doc):
                return {"id": doc["id"], "revision": row["revision"], "existing": True}
            if row and row["revision"] != expected:
                raise Conflict("Document revision changed; read and explicitly retry")
            if row and row["control"] in ("active", "pause-requested"):
                raise Conflict("Pause this execution before changing its goal/scope")
            rev = row["revision"] + 1 if row else 1
            if row:
                c.execute(
                    "UPDATE tasks SET status='cancelled',generation=generation+1 WHERE track=? AND status IN ('queued','waiting')",
                    (doc["id"],),
                )
                c.execute(
                    "UPDATE decisions SET status='superseded' WHERE track=? AND status='open'",
                    (doc["id"],),
                )
                c.execute(
                    "UPDATE tracks SET revision=?,document=?,status=?,control=?,review=NULL,"
                    "verification=NULL,updated=? WHERE id=?",
                    (rev, encode(doc), "open", "idle", time.time(), doc["id"]),
                )
                if row["status"] == "done":
                    self.event(
                        c,
                        "delivery.archived",
                        doc["id"],
                        {
                            k: row[k]
                            for k in (
                                "revision",
                                "request",
                                "branch",
                                "workspace",
                                "issue",
                                "pr",
                                "head",
                                "landing",
                            )
                        },
                    )
                    c.execute(
                        "UPDATE tracks SET branch=NULL,workspace=NULL,issue=NULL,pr=NULL,head=NULL,landing=NULL,request=NULL WHERE id=?",
                        (doc["id"],),
                    )
            else:
                c.execute(
                    "INSERT INTO tracks(id,revision,document,updated) VALUES(?,?,?,?)",
                    (doc["id"], rev, encode(doc), time.time()),
                )
            c.execute("INSERT INTO documents VALUES(?,?,?)", (doc["id"], rev, encode(doc)))
            self.event(c, "document.registered", doc["id"], {"revision": rev})
        return {"id": doc["id"], "revision": rev}

    def enqueue(self, c, track, kind, purpose, key):
        if kind not in (
            "assess",
            "work",
            "verify",
            "review",
            "land",
            "triage",
            "complete",
            "watch",
        ):
            raise ValueError("Unknown task kind: " + kind)
        if kind in ("review", "land", "triage", "complete", "verify"):
            pending = c.execute(
                "SELECT id FROM tasks WHERE track=? AND kind=? AND status IN ('queued','running','waiting') ORDER BY created LIMIT 1",
                (track, kind),
            ).fetchone()
            if pending:
                self.event(
                    c,
                    "work.joined",
                    track,
                    {"workId": pending[0], "kind": kind, "purpose": purpose},
                )
                return pending[0]
        id_ = uid("work")
        c.execute(
            "INSERT OR IGNORE INTO tasks(id,track,kind,purpose,dedup,created,updated) "
            "VALUES(?,?,?,?,?,?,?)",
            (id_, track, kind, purpose, key, time.time(), time.time()),
        )
        row = c.execute("SELECT id FROM tasks WHERE dedup=?", (key,)).fetchone()
        self.event(c, "work.requested", track, {"workId": row[0], "kind": kind, "purpose": purpose})
        return row[0]

    def start(self, track, request=None):
        with self.transaction() as c:
            t = self.track(track, c)
            if t["status"] == "done":
                raise Conflict("Track is complete; revise the document for new scope")
            if t["control"] in ("active", "paused", "pause-requested"):
                return {"requestId": t["request"], "existing": True, "control": t["control"]}
            request = request or uid("request")
            c.execute(
                "UPDATE tracks SET request=?,control='active',updated=? WHERE id=?",
                (request, time.time(), track),
            )
            self.enqueue(
                c,
                track,
                "assess",
                "Read the goal and choose the next useful bounded work",
                track + ":" + request + ":initial",
            )
            self.event(c, "execution.accepted", track, {"requestId": request})
        return {"requestId": request, "existing": False}

    def control(self, track, action):
        if action not in ("pause", "resume", "cancel"):
            raise ValueError("Expected pause/resume/cancel")
        with self.transaction() as c:
            t = self.track(track, c)
            if t["status"] == "done":
                raise Conflict("Already complete")
            if action == "resume" and t["control"] not in ("paused", "pause-requested"):
                raise Conflict("Only paused requests can resume")
            active = c.execute(
                "SELECT 1 FROM tasks WHERE track=? AND status='running'", (track,)
            ).fetchone()
            state = (
                ("pause-requested" if active else "paused")
                if action == "pause"
                else ("active" if action == "resume" else "cancelled")
            )
            c.execute(
                "UPDATE tracks SET control=?,updated=? WHERE id=?", (state, time.time(), track)
            )
            if action == "cancel":
                c.execute(
                    "UPDATE tasks SET status='cancelled',generation=generation+1,updated=? "
                    "WHERE track=? AND status IN ('queued','waiting','running')",
                    (time.time(), track),
                )
            self.event(c, "execution.control", track, {"action": action, "control": state})
        return state

    def claim(self, owner, ttl=45):
        with self.transaction() as c:
            # One mutable checkout per track. Different tracks can proceed concurrently.
            row = c.execute(
                "SELECT w.* FROM tasks w JOIN tracks t ON t.id=w.track "
                "WHERE w.status='queued' AND t.control='active' "
                "AND NOT EXISTS(SELECT 1 FROM tasks a WHERE a.track=w.track "
                "AND a.status='running') ORDER BY w.created LIMIT 1"
            ).fetchone()
            if not row:
                return None
            t = self.track(row["track"], c)
            attempt = uid("attempt")
            generation = row["generation"] + 1
            c.execute(
                "UPDATE tasks SET status='running',owner=?,lease=?,generation=?,"
                "input_revision=?,attempts=attempts+1,updated=? WHERE id=?",
                (owner, time.time() + ttl, generation, t["revision"], time.time(), row["id"]),
            )
            c.execute(
                "INSERT INTO attempts(id,task,generation,status,started) VALUES(?,?,?,?,?)",
                (attempt, row["id"], generation, "running", time.time()),
            )
            self.event(
                c,
                "worker.claimed",
                t["id"],
                {"attemptId": attempt, "workId": row["id"], "owner": owner, "kind": row["kind"]},
            )
            return {
                **dict(row),
                "generation": generation,
                "attempt": attempt,
                "input_revision": t["revision"],
                "owner": owner,
            }

    def assert_claim(self, c, task, allow_paused=True):
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        t = self.track(task["track"], c)
        allowed = ("active", "pause-requested") if allow_paused else ("active",)
        if (
            not row
            or row["status"] != "running"
            or row["generation"] != task["generation"]
            or row["owner"] != task["owner"]
            or (row["lease"] or 0) < time.time()
            or t["revision"] != task["input_revision"]
            or t["control"] not in allowed
        ):
            raise Conflict("Stale claim, changed goal, or stopped request")

    def heartbeat(self, task, pid=None):
        with self.transaction() as c:
            self.assert_claim(c, task)
            c.execute(
                "UPDATE tasks SET lease=?,updated=? WHERE id=?",
                (time.time() + 45, time.time(), task["id"]),
            )
            if pid:
                c.execute("UPDATE attempts SET pid=? WHERE id=?", (pid, task["attempt"]))

    def finish(self, task, result, connection=None):
        with (
            contextlib.nullcontext(connection)
            if connection is not None
            else self.transaction() as c
        ):
            self.assert_claim(c, task)
            result_id = uid("result")
            c.execute(
                "INSERT INTO results VALUES(?,?,?,?,?)",
                (result_id, task["id"], task["attempt"], encode(result), time.time()),
            )
            for index, finding in enumerate(result.get("findings", [])):
                if not all(finding.get(k) for k in ("observation", "evidence")):
                    raise ValueError("Finding requires observation and evidence")
                id_ = "finding-" + fingerprint([task["id"], index, finding])[:20]
                body = {**finding, "origin": task["kind"], "attempt": task["attempt"]}
                c.execute(
                    "INSERT OR IGNORE INTO findings VALUES(?,?,?,?,?)",
                    (id_, task["track"], encode(body), "open", time.time()),
                )
            wait = result.get("question")
            dependencies = result.get("wait_for", [])
            c.execute(
                "UPDATE tasks SET status=?,lease=NULL,updated=? WHERE id=?",
                ("waiting" if wait or dependencies else "done", time.time(), task["id"]),
            )
            c.execute(
                "UPDATE attempts SET status='finished',finished=?,result=? WHERE id=?",
                (time.time(), result_id, task["attempt"]),
            )
            for dep in dependencies:
                if (
                    dep == task["id"]
                    or not c.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone()
                ):
                    raise ValueError("Invalid dependency")
                c.execute("INSERT OR IGNORE INTO waits VALUES(?,?)", (task["id"], dep))
            if wait:
                c.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        uid("decision"),
                        task["track"],
                        task["id"],
                        wait,
                        "open",
                        task["input_revision"],
                        None,
                        time.time(),
                    ),
                )
            else:
                for next_ in result.get("next", []):
                    key = fingerprint([task["id"], next_["kind"], next_["purpose"]])
                    self.enqueue(c, task["track"], next_["kind"], next_["purpose"], key)
            self.event(
                c,
                "work.result",
                task["track"],
                {"resultId": result_id, "workId": task["id"], "summary": result["summary"]},
            )
            c.execute(
                "UPDATE tracks SET control='paused' WHERE id=? AND control='pause-requested'",
                (task["track"],),
            )
        return result_id

    def answer(self, id_, answer):
        if not answer.strip():
            raise ValueError("Answer must not be empty")
        with self.transaction() as c:
            d = c.execute("SELECT * FROM decisions WHERE id=?", (id_,)).fetchone()
            if not d or d["status"] != "open":
                raise Conflict("Decision not open")
            t = self.track(d["track"], c)
            if t["revision"] != d["revision"]:
                raise Conflict("Question belongs to an old goal revision")
            c.execute("UPDATE decisions SET status='answered',answer=? WHERE id=?", (answer, id_))
            c.execute(
                "UPDATE tasks SET status='done' WHERE id=? AND status='waiting'", (d["task"],)
            )
            kind = c.execute("SELECT kind FROM tasks WHERE id=?", (d["task"],)).fetchone()[0]
            repair = json.loads(t["landing"]) if t["landing"] else {}
            resume_kind = "triage" if kind == "triage" else "assess"
            if repair.get("status") == "integration-repair":
                resume_kind = "work"
            self.enqueue(
                c,
                d["track"],
                resume_kind,
                "Continue using decision answer: " + answer,
                id_,
            )
            self.event(c, "decision.answered", d["track"], {"decisionId": id_, "answer": answer})

    def snapshot(self):
        with self.connect() as c:
            tables = (
                "tracks",
                "tasks",
                "attempts",
                "results",
                "decisions",
                "effects",
                "watches",
                "findings",
                "triages",
            )
            out = {t: [dict(r) for r in c.execute("SELECT * FROM " + t)] for t in tables}
            out["events"] = [
                dict(r) for r in c.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 200")
            ]
        out["observedAt"] = time.time()
        return out
