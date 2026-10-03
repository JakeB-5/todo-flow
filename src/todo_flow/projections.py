"""Bounded read models. Dashboard queries never enumerate execution history in Python."""

import json
import time
import hashlib

from . import condition_evidence
from .language import LANGUAGES
from .launch_display import describe_launch, read_launch
from .verification_log_view import log_view, read_log_range
from .worker_display import read_worker


SUMMARY = """t.id,t.revision,t.status,t.control,t.issue,t.pr,t.updated,
 json_extract(t.document,'$.title') AS title,
 json_extract(t.document,'$.goal') AS goal,
 json_extract(t.document,'$.trigger') AS trigger,
 json_extract(t.document,'$.group') AS track_group,
 COALESCE(json_extract(t.document,'$.priority'),'Unspecified') AS priority,
 COALESCE(json_extract(t.document,'$.area'),'General') AS area"""


# Only the runtime's explicit repair instruction has a more specific display intent.
# Legacy/free-form purposes remain available through task(), never sliced into summaries.
TASK_SUMMARY = """id,track,kind,status,owner,lease,updated,
 CASE WHEN kind='work' AND substr(purpose,1,18)='Fix verification: '
 THEN 'verification-repair'
 WHEN kind='assess' AND substr(purpose,1,23)='Diagnose verification: '
 THEN 'verification-diagnosis' ELSE NULL END AS intent"""
TASK_ORDER = (
    "CASE status WHEN 'running' THEN 0 WHEN 'waiting' THEN 1 "
    "WHEN 'queued' THEN 2 ELSE 3 END,created,id"
)


def bounds(limit=25, offset=0):
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1..100; offset must be nonnegative")
    return limit, offset


def page(c, select, where, params, order, limit, offset):
    total = c.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
    offset = min(offset, max(0, ((total - 1) // limit) * limit)) if total else 0
    items = [
        dict(r)
        for r in c.execute(
            select + " " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(items) < total,
    }


class Dashboard:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.executescript("""
            CREATE INDEX IF NOT EXISTS tracks_status_updated ON tracks(status,updated DESC,id);
            CREATE INDEX IF NOT EXISTS tracks_control ON tracks(control,status);
            CREATE INDEX IF NOT EXISTS tasks_track_status ON tasks(track,status,created);
            CREATE INDEX IF NOT EXISTS tasks_status_updated ON tasks(status,updated DESC,id);
            CREATE INDEX IF NOT EXISTS decisions_status_track ON decisions(status,track);
            CREATE INDEX IF NOT EXISTS events_track_seq ON events(track,seq DESC);
            CREATE INDEX IF NOT EXISTS watches_status_created ON watches(status,created DESC,id);
            CREATE INDEX IF NOT EXISTS attempts_task_started ON attempts(task,started DESC);
            CREATE INDEX IF NOT EXISTS results_task_created ON results(task,created DESC);
            """)

    def overview(self):
        with self.store.connect() as c:
            c.execute("BEGIN")
            counts = dict(
                c.execute("""SELECT
              COUNT(*) AS total,
              SUM(status='done') AS completed,
              SUM(status<>'done') AS active,
              SUM(status<>'done' AND control NOT IN ('active','paused','pause-requested')) AS ready,
              SUM(status<>'done' AND control IN ('paused','pause-requested')) AS paused
              FROM tracks""").fetchone()
            )
            counts = {k: v or 0 for k, v in counts.items()}
            counts["running"] = c.execute(
                "SELECT COUNT(DISTINCT w.track) FROM tasks w JOIN tracks t ON t.id=w.track WHERE w.status='running' AND w.lease>? AND t.status<>'done'",
                (time.time(),),
            ).fetchone()[0]
            counts["decisions"] = c.execute(
                "SELECT COUNT(*) FROM decisions d JOIN tracks t ON t.id=d.track WHERE d.status='open' AND t.control NOT IN ('cancelled','finished')"
            ).fetchone()[0]
            counts["watch"] = c.execute(
                "SELECT COUNT(*) FROM watches WHERE status='open'"
            ).fetchone()[0]
            revision = c.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        config = self.store.config()
        return {
            "counts": counts,
            "revision": revision,
            "observedAt": time.time(),
            "project": {
                **{
                    k: config.get(k) for k in ("github", "base", "endpoint", "display_name", "demo")
                },
                "language": config.get("language", "en"),
                "key": hashlib.sha256(str(self.store.path).encode()).hexdigest()[:24],
            },
        }

    def tracks(
        self, view="active", q="", control="all", sort="updated", limit=25, offset=0, as_of=None
    ):
        limit, offset = bounds(limit, offset)
        if view not in ("active", "completed"):
            raise ValueError("view must be active or completed")
        order = {
            "updated": "t.updated DESC,t.id ASC",
            "title": "json_extract(t.document,'$.title') COLLATE NOCASE,t.id ASC",
            "oldest": "t.updated ASC,t.id ASC",
        }.get(sort)
        if not order:
            raise ValueError("Unknown sort")
        where = ["t.status='done'" if view == "completed" else "t.status<>'done'"]
        params = []
        if q:
            if len(q) > 200:
                raise ValueError("Search too long")
            literal = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(
                "(t.id LIKE ? ESCAPE '\\' OR json_extract(t.document,'$.title') LIKE ? ESCAPE '\\' OR json_extract(t.document,'$.goal') LIKE ? ESCAPE '\\' OR json_extract(t.document,'$.area') LIKE ? ESCAPE '\\')"
            )
            params += [literal] * 4
        if control == "ready":
            where.append("t.control NOT IN ('active','paused','pause-requested')")
        elif control == "waiting":
            where.append("EXISTS(SELECT 1 FROM tasks w WHERE w.track=t.id AND w.status='waiting')")
        elif control == "running":
            where.append(
                "EXISTS(SELECT 1 FROM tasks w WHERE w.track=t.id AND w.status='running' AND w.lease>?)"
            )
            params.append(time.time())
        elif control != "all":
            if control not in (
                "active",
                "idle",
                "paused",
                "pause-requested",
                "cancelled",
                "finished",
            ):
                raise ValueError("Unknown control filter")
            where.append("t.control=?")
            params.append(control)
        anchor = float(as_of) if as_of else time.time()
        if not 0 < anchor < 1e12:
            raise ValueError("Invalid snapshot timestamp")
        if view == "completed":
            where.append("t.updated<=?")
            params.append(anchor)
        source = "FROM tracks t WHERE " + " AND ".join(where)
        with self.store.connect() as c:
            c.execute("BEGIN")
            result = page(c, "SELECT " + SUMMARY, source, params, order, limit, offset)
            for t in result["items"]:
                t["selectable"] = t["status"] != "done" and t["control"] not in (
                    "active",
                    "paused",
                    "pause-requested",
                )
                t["activity"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,kind,purpose,status,owner,updated,lease FROM tasks WHERE track=? AND status IN ('queued','running','waiting') ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,created LIMIT 2",
                        (t["id"],),
                    )
                ]
        result["asOf"] = anchor
        result["view"] = view
        return result

    def detail(self, id_):
        t = self.store.track(id_)
        for key in ("document",):
            t[key] = json.loads(t[key])
        with self.store.connect() as c:
            triage = c.execute(
                "SELECT id,body,created FROM triages WHERE track=? ORDER BY created DESC LIMIT 1",
                (id_,),
            ).fetchone()
        t["triage"] = (
            {"id": triage["id"], "at": triage["created"], **json.loads(triage["body"])}
            if triage
            else None
        )
        presentation = t["document"].pop("presentation", None)
        t["documentView"] = {
            "url": f"/documents/{id_}/{t['revision']}/index.html",
            "format": presentation["format"] if presentation else "markdown",
            "assets": len(presentation.get("assets", [])) if presentation else 0,
        }
        # Recheck explicit references before displaying a current integrity assertion.
        review = condition_evidence.review_view(self.store.path, t)
        # Heavy evidence is fetched only on demand, never shipped with a list or the initial detail.
        for key in ("verification", "review", "landing"):
            value = review if key == "review" else json.loads(t[key]) if t[key] else None
            t[key] = (
                {
                    k: value[k]
                    for k in ("ok", "verdict", "head", "merged", "at", "conditions", "receipt")
                    if k in value
                }
                if value
                else None
            )
        return t

    def evidence(self, id_, kind, **params):
        if kind not in ("verification", "review", "landing"):
            raise ValueError("Unknown evidence kind")
        t = self.store.track(id_)
        value = json.loads(t[kind]) if t[kind] else None
        if kind == "verification":
            if params:
                value = read_log_range(self.store.path, id_, value, **params)
            else:
                logs = log_view(self.store.path, id_, value)
                if value is not None or logs["latestExecution"] is not None:
                    value = {
                        **(value or {}),
                        "logs": logs,
                        "evidenceArtifacts": condition_evidence.available(self.store.path, t),
                    }
        elif params:
            raise ValueError("Log ranges require verification evidence")
        elif kind == "review":
            value = condition_evidence.review_view(self.store.path, t)
        return {"track": id_, "kind": kind, "value": value}

    def activity(self, limit=25, offset=0):
        limit, offset = bounds(limit, offset)
        now = time.time()
        source = """FROM tracks t
          LEFT JOIN (
            SELECT track,COUNT(*) AS task_count,
              SUM(status='running') AS running,
              SUM(status='queued') AS queued,
              SUM(status='waiting') AS waiting,
              SUM(status='running' AND (lease IS NULL OR lease<=?)) AS uncertain
            FROM tasks WHERE status IN ('queued','running','waiting') GROUP BY track
          ) a ON a.track=t.id
          LEFT JOIN (
            SELECT track,COUNT(*) AS decisions FROM decisions
            WHERE status='open' GROUP BY track
          ) d ON d.track=t.id
          WHERE t.status<>'done' AND t.control NOT IN ('cancelled','finished')
            AND (a.task_count>0 OR d.decisions>0)"""
        with self.store.connect() as c:
            c.execute("BEGIN")
            result = page(
                c,
                """SELECT t.id,json_extract(t.document,'$.title') AS title,t.control,
                  COALESCE(a.task_count,0) AS taskCount,
                  COALESCE(a.running,0) AS running,
                  COALESCE(a.queued,0) AS queued,
                  COALESCE(a.waiting,0) AS waiting,
                  COALESCE(a.uncertain,0) AS uncertain,
                  COALESCE(d.decisions,0) AS decisions,
                  json_extract(t.verification,'$.ok') AS verificationOk,
                  json_extract(t.verification,'$.outcome') AS verificationOutcome,
                  json_extract(t.verification,'$.complete') AS verificationComplete,
                  json_extract(t.verification,'$.cancelled') AS verificationCancelled,
                  json_extract(t.verification,'$.at') AS verificationAt,
                  CASE WHEN a.running>0 THEN 'running'
                    WHEN a.waiting>0 OR d.decisions>0 THEN 'waiting'
                    ELSE 'queued' END AS status""",
                source,
                [now],
                "t.id",
                limit,
                offset,
            )
            result["taskTotal"] = c.execute(
                "SELECT COALESCE(SUM(a.task_count),0) " + source, (now,)
            ).fetchone()[0]
            for track in result["items"]:
                task = c.execute(
                    "SELECT "
                    + TASK_SUMMARY
                    + " FROM tasks WHERE track=? AND status IN ('queued','running','waiting')"
                    + " ORDER BY "
                    + TASK_ORDER
                    + " LIMIT 1",
                    (track["id"],),
                ).fetchone()
                track["current"] = dict(task) if task else None
                if task:
                    attempt = c.execute(
                        "SELECT id FROM attempts WHERE task=?"
                        " ORDER BY started DESC,id DESC LIMIT 1",
                        (task["id"],),
                    ).fetchone()
                    track["current"]["worker"] = read_worker(
                        self.store.path, attempt["id"] if attempt else None
                    )
        result["observedAt"] = now
        return result

    def activity_tasks(self, track, limit=10, offset=0):
        limit, offset = bounds(limit, offset)
        with self.store.connect() as c:
            c.execute("BEGIN")
            return page(
                c,
                "SELECT " + TASK_SUMMARY,
                "FROM tasks WHERE track=?",
                [track],
                TASK_ORDER,
                limit,
                offset,
            )

    def decisions(self, limit=25, offset=0, track=None):
        limit, offset = bounds(limit, offset)
        with self.store.connect() as c:
            c.execute("BEGIN")
            return page(
                c,
                "SELECT d.*,json_extract(t.document,'$.title') AS title",
                "FROM decisions d JOIN tracks t ON t.id=d.track WHERE d.status='open' AND t.control NOT IN ('cancelled','finished')"
                + (" AND d.track=?" if track else ""),
                [track] if track else [],
                "d.created,d.id",
                limit,
                offset,
            )

    def events(self, track=None, before=None, limit=25):
        limit, _ = bounds(limit)
        where = []
        args = []
        if track:
            where.append("track=?")
            args.append(track)
        if before:
            where.append("seq<?")
            args.append(int(before))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self.store.connect() as c:
            # Event summary projection excludes full verification logs / remote effect payloads.
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT seq,type,track,at,substr(COALESCE(json_extract(body,'$.summary'),json_extract(body,'$.error'),json_extract(body,'$.purpose'),json_extract(body,'$.kind'),''),1,350) AS summary FROM events"
                    + clause
                    + " ORDER BY seq DESC LIMIT ?",
                    (*args, limit + 1),
                )
            ]
        return {
            "items": rows[:limit],
            "next": rows[limit - 1]["seq"] if len(rows) > limit else None,
        }

    def task(self, id_):
        with self.store.connect() as c:
            w = c.execute("SELECT * FROM tasks WHERE id=?", (id_,)).fetchone()
            if not w:
                raise ValueError("Unknown task")
            a = c.execute(
                "SELECT * FROM attempts WHERE task=? ORDER BY started DESC,id DESC LIMIT 1", (id_,)
            ).fetchone()
            r = c.execute(
                "SELECT * FROM results WHERE task=? ORDER BY created DESC LIMIT 1", (id_,)
            ).fetchone()
        # Reuse the response attempt; a second lookup could select a newer attempt.
        launch = read_launch(self.store.path, a["id"] if a else None)
        record = launch["record"] or {}
        public = {}
        if record.get("execution_mode") == "orca-native":
            public["execution_mode"] = "orca-native"
            for key in ("worktree", "session", "turn"):
                if isinstance(record.get(key), str):
                    public[key] = record[key]
            terminal = record.get("terminal")
            if isinstance(terminal, dict) and isinstance(terminal.get("handle"), str):
                public["terminal"] = {"handle": terminal["handle"]}
        return {
            "task": dict(w),
            "attempt": dict(a) if a else None,
            "worker": read_worker(self.store.path, a["id"] if a else None),
            "result": json.loads(r["body"]) if r else None,
            # Expose runtime labels and explicitly selected resource identifiers only.
            "launch": {
                **({"record": public} if public else {}),
                "attempt": launch["attempt"],
                "evidence": launch["evidence"],
                "summaries": {
                    language: describe_launch(launch["record"] or {}, language)
                    for language in LANGUAGES
                },
            },
        }

    def watches(self, status="open", limit=25, offset=0):
        limit, offset = bounds(limit, offset)
        if status not in ("open", "resolved", "dismissed", "promoted", "all"):
            raise ValueError("Unknown watch status")
        where = "FROM watches" + ("" if status == "all" else " WHERE status=?")
        with self.store.connect() as c:
            c.execute("BEGIN")
            result = page(
                c,
                "SELECT *",
                where,
                [] if status == "all" else [status],
                "created DESC,id",
                limit,
                offset,
            )
        for row in result["items"]:
            row["body"] = json.loads(row["body"])
        return result
