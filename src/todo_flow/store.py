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
        from .worker_routing import plan

        plan(doc.get("workerPlan"))
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

    def enqueue(self, c, track, kind, purpose, key, *, parent=None):
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
        source = {}
        obligation_head = obligation_revision = None
        followup = parent is not None and kind in ("assess", "work")
        if parent is not None:
            t = self.track(track, c)
            source = {
                "parentWorkId": parent["id"],
                "parentAttemptId": parent["attempt"],
                "requestKey": key,
                "head": t["head"],
                "documentRevision": t["revision"],
            }
            if followup:
                # Enrollment is immutable; claim-time input_revision is a separate fence.
                obligation_head, obligation_revision = t["head"], t["revision"]
        if followup and obligation_head:
            pending = c.execute(
                "SELECT id FROM tasks WHERE track=? AND kind=? AND purpose=? "
                "AND obligation_head=? AND obligation_revision=? "
                "AND status IN ('queued','running','waiting') AND id<>? "
                "ORDER BY created LIMIT 1",
                (track, kind, purpose, obligation_head, obligation_revision, parent["id"]),
            ).fetchone()
            if pending:
                self.event(
                    c,
                    "work.joined",
                    track,
                    {"workId": pending[0], "kind": kind, "purpose": purpose, **source},
                )
                return pending[0]
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
                    {"workId": pending[0], "kind": kind, "purpose": purpose, **source},
                )
                return pending[0]
        id_ = uid("work")
        # A parent's old key is provenance, not evidence that a done obligation is met.
        dedup = fingerprint([key, id_]) if followup else key
        c.execute(
            "INSERT OR IGNORE INTO tasks"
            "(id,track,kind,purpose,dedup,created,updated,obligation_head,obligation_revision) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                id_,
                track,
                kind,
                purpose,
                dedup,
                time.time(),
                time.time(),
                obligation_head,
                obligation_revision,
            ),
        )
        row = c.execute("SELECT id FROM tasks WHERE dedup=?", (dedup,)).fetchone()
        self.event(
            c,
            "work.requested",
            track,
            {"workId": row[0], "kind": kind, "purpose": purpose, **source},
        )
        return row[0]

    @staticmethod
    def positive_limit(value):
        if type(value) is not int or value < 1:
            raise ValueError("Worker attempt limits must be positive integers")
        return value

    def budget(self, c, track, worker_limit=None):
        if not track["request"]:
            return None
        id_ = "budget-" + fingerprint([track["id"], track["request"]])[:24]
        c.execute(
            "INSERT OR IGNORE INTO budgets(id,track,request,worker_limit) VALUES(?,?,?,?)",
            (id_, track["id"], track["request"], worker_limit),
        )
        return dict(c.execute("SELECT * FROM budgets WHERE id=?", (id_,)).fetchone())

    def budget_details(self, c, track, budget):
        doc = json.loads(track["document"])
        remaining = [
            dict(row)
            for row in c.execute(
                "SELECT id,kind,purpose,status FROM tasks WHERE track=? "
                "AND status IN ('queued','running','waiting') ORDER BY created,id",
                (track["id"],),
            )
        ]
        return {
            **budget,
            "candidate_head": track["head"],
            "goal": doc["goal"],
            "required_conditions": doc["conditions"],
            "remaining_tasks": remaining,
        }

    def budget_status(self):
        with self.connect() as c:
            rows = c.execute(
                "SELECT b.* FROM budgets b JOIN tracks t ON t.id=b.track "
                "AND t.request=b.request ORDER BY b.track"
            ).fetchall()
            return [self.budget_details(c, self.track(row["track"], c), dict(row)) for row in rows]

    def stop_for_budget(self, c, track, task, budget):
        if not budget["decision"]:
            decision = uid("decision")
            budget = {**budget, "decision": decision}
            details = self.budget_details(c, track, budget)
            question = (
                "Worker attempt budget exhausted. Pending obligations and candidate are preserved. "
                "Approve additional attempts with answer --additional-worker-attempts N. "
                + encode(details)
            )
            c.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    decision,
                    track["id"],
                    task["id"],
                    question,
                    "open",
                    track["revision"],
                    None,
                    time.time(),
                ),
            )
            c.execute("UPDATE budgets SET decision=? WHERE id=?", (decision, budget["id"]))
            self.event(c, "execution.budget-exhausted", track["id"], details)
        c.execute(
            "UPDATE tracks SET control='paused',updated=? WHERE id=?",
            (time.time(), track["id"]),
        )

    def extend_budget(self, c, track, decision, budget, answer, additional):
        self.positive_limit(additional)
        if track["request"] != budget["request"] or track["control"] == "cancelled":
            raise Conflict("Budget decision belongs to an inactive request")
        if budget["worker_limit"] is None:
            raise Conflict("This request has no worker attempt limit")
        previous_limit = budget["worker_limit"]
        budget = {
            **budget,
            "worker_limit": previous_limit + additional,
            "decision": None,
        }
        c.execute(
            "UPDATE budgets SET worker_limit=?,decision=NULL WHERE id=?",
            (budget["worker_limit"], budget["id"]),
        )
        c.execute(
            "UPDATE decisions SET status='answered',answer=? WHERE id=?",
            (answer, decision["id"]),
        )
        c.execute(
            "UPDATE tracks SET control='active',updated=? WHERE id=?",
            (time.time(), track["id"]),
        )
        details = self.budget_details(c, track, budget)
        self.event(
            c,
            "execution.budget-extended",
            track["id"],
            {
                **details,
                "decisionId": decision["id"],
                "previous_limit": previous_limit,
                "additional_worker_attempts": additional,
                "answer": answer,
            },
        )
        self.event(
            c, "decision.answered", track["id"], {"decisionId": decision["id"], "answer": answer}
        )
        return details

    def routing(self, c, track, mode=None, selections=None):
        """Request snapshots live in canonical events, in the caller's transaction."""
        from .worker_routing import request as routing_request

        previous = None
        for row in c.execute(
            "SELECT body FROM events WHERE track=? AND type='execution.routing' ORDER BY seq DESC",
            (track["id"],),
        ):
            candidate = json.loads(row[0])
            if candidate["requestId"] == track["request"]:
                previous = candidate
                break
        if previous is not None and mode is None and selections is None:
            return previous
        row = c.execute("SELECT body FROM config WHERE id=1").fetchone()
        config = json.loads(row[0]) if row else {}
        document = json.loads(track["document"])
        if (
            previous is None
            and mode is None
            and selections is None
            and not document.get("workerPlan")
            and not config.get("worker_profiles")
        ):
            # Preserve legacy single/custom adapter behavior until routing is selected.
            return None
        current = {
            **routing_request(previous, config, document, mode, selections),
            "requestId": track["request"],
        }
        if current != previous:
            self.event(c, "execution.routing", track["id"], current)
        return current

    def select_worker(self, c, track, kind):
        from .worker_routing import resolve

        routing = self.routing(c, track)
        if routing is None:
            return None
        try:
            return resolve(routing, kind)
        except ValueError as error:
            # Claim once and let the host open a decision without launching a provider.
            return {
                "version": 1,
                "mode": routing["mode"],
                "role": kind,
                "requestId": routing["requestId"],
                "error": str(error),
            }

    def start(self, track, request=None, worker_limit=None, worker_mode=None, worker_roles=None):
        if worker_limit is not None:
            self.positive_limit(worker_limit)
        with self.transaction() as c:
            t = self.track(track, c)
            if t["status"] == "done":
                raise Conflict("Track is complete; revise the document for new scope")
            if t["control"] in ("active", "paused", "pause-requested"):
                budget = self.budget(c, t)
                if worker_limit is not None and budget["worker_limit"] != worker_limit:
                    raise Conflict(
                        "Existing request limit is immutable; answer its budget decision"
                    )
                routing = self.routing(c, t, worker_mode, worker_roles)
                return {
                    "requestId": t["request"],
                    "existing": True,
                    "control": t["control"],
                    "budget": budget,
                    "routing": routing,
                }
            request = request or uid("request")
            c.execute(
                "UPDATE tracks SET request=?,control='active',updated=? WHERE id=?",
                (request, time.time(), track),
            )
            t = self.track(track, c)
            routing = self.routing(c, t, worker_mode, worker_roles)
            budget = self.budget(c, t, worker_limit)
            if worker_limit is not None and budget["worker_limit"] != worker_limit:
                raise Conflict("Reusing a request ID cannot replace its persisted limit")
            self.enqueue(
                c,
                track,
                "assess",
                "Read the goal and choose the next useful bounded work",
                track + ":" + request + ":initial",
            )
            self.event(c, "execution.accepted", track, {"requestId": request, "budget": budget})
        return {"requestId": request, "existing": False, "budget": budget, "routing": routing}

    def control(self, track, action):
        if action not in ("pause", "resume", "cancel"):
            raise ValueError("Expected pause/resume/cancel")
        with self.transaction() as c:
            t = self.track(track, c)
            if t["status"] == "done":
                raise Conflict("Already complete")
            if action == "resume" and t["control"] not in ("paused", "pause-requested"):
                raise Conflict("Only paused requests can resume")
            if action == "resume":
                budget = self.budget(c, t)
                if budget and budget["decision"]:
                    raise Conflict(
                        "Answer the budget decision with explicit additional worker attempts"
                    )
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
                from .cancel_execution import record_requests

                record_requests(self, c, track)
                c.execute(
                    "UPDATE tasks SET status='cancelled',generation=generation+1,updated=? "
                    "WHERE track=? AND status IN ('queued','waiting','running')",
                    (time.time(), track),
                )
            self.event(c, "execution.control", track, {"action": action, "control": state})
        return state

    def claim(self, owner, ttl=45, *, connection=None):
        from .adapters import file_lock

        with (
            contextlib.nullcontext(connection)
            if connection is not None
            else self.transaction() as c
        ):
            # One mutable checkout per track. Budget checks share the claim transaction.
            rows = c.execute(
                "SELECT w.* FROM tasks w JOIN tracks t ON t.id=w.track "
                "WHERE w.status='queued' AND t.control='active' "
                "AND NOT EXISTS(SELECT 1 FROM tasks a WHERE a.track=w.track "
                "AND a.status='running') ORDER BY w.created,w.id"
            ).fetchall()
            for row in rows:
                # A finished task may still own its process_attempt lock. Probe without
                # waiting while holding the transaction, before consuming a claim/budget.
                # Execution retains its own lock and claim checks against later owners.
                try:
                    with file_lock(self.path / "locks" / (row["track"] + ".lock")):
                        pass
                except Conflict:
                    continue
                t = self.track(row["track"], c)
                if t["control"] != "active":
                    continue
                budget = self.budget(c, t)
                # These three kinds execute host actions; every other kind invokes a worker.
                worker_attempt = row["kind"] not in ("verify", "land", "complete")
                if budget and worker_attempt:
                    if (
                        budget["worker_limit"] is not None
                        and budget["used"] >= budget["worker_limit"]
                    ):
                        self.stop_for_budget(c, t, row, budget)
                        continue
                    c.execute("UPDATE budgets SET used=used+1 WHERE id=?", (budget["id"],))
                    budget = {**budget, "used": budget["used"] + 1}
                selection = self.select_worker(c, t, row["kind"]) if worker_attempt else None
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
                    {
                        "attemptId": attempt,
                        "workId": row["id"],
                        "owner": owner,
                        "kind": row["kind"],
                        "budget": budget,
                        "worker_attempt": worker_attempt,
                        "worker_selection": selection,
                    },
                )
                return {
                    **dict(row),
                    "generation": generation,
                    "attempt": attempt,
                    "input_revision": t["revision"],
                    "owner": owner,
                    "worker_selection": selection,
                }
            return None

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
                    self.enqueue(
                        c, task["track"], next_["kind"], next_["purpose"], key, parent=task
                    )
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

    def answer(self, id_, answer, additional_worker_attempts=None):
        if not answer.strip():
            raise ValueError("Answer must not be empty")
        if additional_worker_attempts is not None:
            self.positive_limit(additional_worker_attempts)
        with self.transaction() as c:
            d = c.execute("SELECT * FROM decisions WHERE id=?", (id_,)).fetchone()
            if not d or d["status"] != "open":
                raise Conflict("Decision not open")
            t = self.track(d["track"], c)
            if t["revision"] != d["revision"]:
                raise Conflict("Question belongs to an old goal revision")
            budget = c.execute("SELECT * FROM budgets WHERE decision=?", (id_,)).fetchone()
            if budget:
                if additional_worker_attempts is None:
                    raise Conflict("Budget resumption requires --additional-worker-attempts N")
                return self.extend_budget(c, t, d, dict(budget), answer, additional_worker_attempts)
            if additional_worker_attempts is not None:
                raise Conflict("Additional attempts require an open budget decision")
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
                "budgets",
            )
            out = {t: [dict(r) for r in c.execute("SELECT * FROM " + t)] for t in tables}
            out["events"] = [
                dict(r) for r in c.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 200")
            ]
        out["observedAt"] = time.time()
        return out
