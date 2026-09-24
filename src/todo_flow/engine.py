"""Durable agenda runner. Agents choose work; the host enforces only effect boundaries."""

import concurrent.futures
import json
import subprocess
import threading
import time
from pathlib import Path

from .adapters import GitHub, command, file_lock, files_for_worker, permitted
from .store import Conflict, encode, fingerprint, uid
from .worker import run_worker


class Engine:
    def __init__(self, store):
        self.store = store
        self.config = store.config()
        self.root = Path(self.config["repo"])
        self.remote = GitHub(store) if self.config.get("github") else None

    def update(self, task, **values):
        allowed = {
            "branch",
            "workspace",
            "issue",
            "pr",
            "head",
            "verification",
            "review",
            "landing",
        }
        if not set(values) <= allowed:
            raise ValueError("Unknown track fields")
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            c.execute(
                "UPDATE tracks SET " + ",".join(k + "=?" for k in values) + ",updated=? WHERE id=?",
                (*values.values(), time.time(), task["track"]),
            )

    def ensure_workspace(self, task):
        # Git worktree registration writes shared .git/config even for different tracks.
        with file_lock(self.store.path / "locks" / "git-metadata.lock", blocking=True):
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
            return self._ensure_workspace(task)

    def _ensure_workspace(self, task):
        t = self.store.track(task["track"])
        if not t["branch"]:
            branch = "todo/" + t["id"] + "-" + t["request"].split("-")[-1][:8]
            workspace = str(self.store.path / "worktrees" / branch.replace("/", "-"))
            self.update(task, branch=branch, workspace=workspace)
            t = self.store.track(task["track"])
        workspace = Path(t["workspace"])
        if not workspace.exists():
            command(["git", "fetch", "origin", self.config["base"]], self.root)
            exists = (
                subprocess.run(
                    ["git", "show-ref", "--verify", "--quiet", "refs/heads/" + t["branch"]],
                    cwd=self.root,
                ).returncode
                == 0
            )
            args = ["git", "worktree", "add"]
            args += (
                [str(workspace), t["branch"]]
                if exists
                else ["-b", t["branch"], str(workspace), "origin/" + self.config["base"]]
            )
            command(args, self.root)
        actual = command(["git", "branch", "--show-current"], workspace)
        if actual != t["branch"]:
            raise Conflict("Workspace branch does not match registered ownership")
        self.update(task, head=command(["git", "rev-parse", "HEAD"], workspace))
        return workspace

    def verify(self, task, workspace):
        head = command(["git", "rev-parse", "HEAD"], workspace)
        tree = command(["git", "rev-parse", "HEAD^{tree}"], workspace)
        key = fingerprint([tree, self.config["verify"]])
        prior = self.store.track(task["track"])["verification"]
        if prior:
            prior = json.loads(prior)
            if prior["key"] == key and prior["ok"]:
                return prior
        log = self.store.path / "attempts" / task["attempt"]
        log.mkdir(parents=True, exist_ok=True)
        started = time.time()
        try:
            output = command(
                self.config["verify"],
                workspace,
                timeout=self.config.get("verify_timeout", 180),
                include_stderr=True,
            )
            ok, error = True, None
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            output, ok, error = str(e), False, type(e).__name__
        if command(["git", "status", "--porcelain"], workspace):
            ok, error = False, "Verification modified the working tree; preserve and inspect it"
        record = {
            "head": head,
            "tree": tree,
            "key": key,
            "command": self.config["verify"],
            "ok": ok,
            "output": output[-12000:],
            "error": error,
            "at": started,
        }
        (log / "verification.json").write_text(encode(record))
        self.update(task, verification=encode(record))
        with self.store.transaction() as c:
            self.store.event(c, "verification.recorded", task["track"], record)
        return record

    def apply_changes(self, task, workspace, changes):
        paths = [x["path"] for x in changes]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate file paths in result")
        for change in changes:
            name = change["path"]
            if not permitted(name, self.config["writable_patterns"]):
                raise Conflict("File is outside the authorized write surface: " + name)
            target = workspace / name
            if not target.resolve().is_relative_to(workspace.resolve()) or target.is_symlink():
                raise Conflict("Symlink/path escape")
            for parent in target.parents:
                if parent == workspace:
                    break
                if parent.is_symlink():
                    raise Conflict("Symlink ancestor")
        # File proposal application and commit run under a checkout flock; each write is fenced.
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            for change in changes:
                target = workspace / change["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change["content"])
        if paths:
            command(["git", "add", "--", *paths], workspace)
            dirty = command(["git", "diff", "--cached", "--name-only"], workspace)
            if dirty:
                command(["git", "commit", "-m", "Implement " + task["track"]], workspace)
        head = command(["git", "rev-parse", "HEAD"], workspace)
        t = self.store.track(task["track"])
        if head != t["head"]:
            self.update(task, head=head, review=None, verification=None, landing=None)

    def publish(self, task, workspace, doc):
        t = self.store.track(task["track"])
        v = json.loads(t["verification"]) if t["verification"] else {}
        if not v.get("ok") or v.get("head") != t["head"]:
            raise Conflict("Publish requires verification of current head")
        with file_lock(self.store.path / "locks" / "publish.lock", blocking=True):
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
            command(["git", "push", "origin", t["branch"]], workspace)
            if self.remote:
                pr = self.remote.pr(task, t, doc)
                self.update(task, pr=pr["number"])

    def context(self, task, workspace):
        t = self.store.track(task["track"])
        snap = self.store.snapshot()
        doc = json.loads(t["document"])
        if "presentation" in doc:
            doc["presentation"]["assets"] = [
                a["path"] for a in doc["presentation"].get("assets", [])
            ]
        return {
            "task": {k: task[k] for k in ("id", "kind", "purpose", "attempt")},
            "document": doc,
            "head": t["head"],
            "endpoint": self.config["endpoint"],
            "language": self.config.get("language", "en"),
            "files": files_for_worker(workspace, self.config["context_patterns"]),
            "diff": command(
                ["git", "diff", "origin/" + self.config["base"] + "...HEAD"], workspace
            )[-100000:],
            "writable_patterns": self.config["writable_patterns"],
            "verification": json.loads(t["verification"]) if t["verification"] else None,
            "review": json.loads(t["review"]) if t["review"] else None,
            "landing": json.loads(t["landing"]) if t["landing"] else None,
            "recent_results": [
                json.loads(r["body"])
                for r in snap["results"]
                if any(w["id"] == r["task"] and w["track"] == t["id"] for w in snap["tasks"])
            ][-6:],
            "decisions": [d for d in snap["decisions"] if d["track"] == t["id"]],
            "watches": [w for w in snap["watches"] if w["track"] == t["id"]],
            "findings": [f for f in snap["findings"] if f["track"] == t["id"]],
            "incoming_findings": [
                json.loads(f["body"])
                for f in snap["findings"]
                if json.loads(f["body"]).get("target") == t["id"]
            ],
        }

    def record_review(self, task, result):
        t = self.store.track(task["track"])
        doc = json.loads(t["document"])
        required = {x["id"] for x in doc["conditions"]}
        rows = result.get("conditions", [])
        if not required <= {x.get("id") for x in rows} or any(
            sum(x.get("id") == id_ for x in rows) != 1 for id_ in required
        ):
            raise ValueError("Reviewer must assess every condition exactly once")
        if any(
            not x.get("evidence") or x.get("verdict") not in ("met", "unmet", "cannot-assess")
            for x in rows
        ):
            raise ValueError("Review condition missing verdict/evidence")
        verdict = result.get("verdict")
        if verdict not in ("met", "unmet", "cannot-assess"):
            raise ValueError("Review verdict required")
        if verdict == "met" and any(x["verdict"] != "met" for x in rows):
            raise ValueError("Contradictory review")
        review = {
            "head": t["head"],
            "documentRevision": t["revision"],
            "attempt": task["attempt"],
            "verdict": verdict,
            "conditions": [x for x in rows if x["id"] in required],
            "additional_assessments": [x for x in rows if x["id"] not in required],
            "summary": result["summary"],
        }
        if self.remote and t["pr"]:
            review["receipt"] = self.remote.post_review(task, t, result)
        self.update(task, review=encode(review))

    def gate(self, task):
        t = self.store.track(task["track"])
        review = json.loads(t["review"]) if t["review"] else {}
        verify = json.loads(t["verification"]) if t["verification"] else {}
        if (
            review.get("verdict") != "met"
            or review.get("head") != t["head"]
            or review.get("documentRevision") != t["revision"]
        ):
            raise Conflict("Current independent review is missing or not met")
        if not verify.get("ok") or verify.get("head") != t["head"]:
            raise Conflict("Current verification missing")
        return t

    def land(self, task):
        if self.config["endpoint"] != "land" or not self.config["allow_land"]:
            raise Conflict("Landing not authorized in project contract")
        with file_lock(self.store.path / "locks" / "landing.lock", blocking=True):
            t = self.gate(task)
            base_ref = "refs/heads/" + self.config["base"]
            command(["git", "fetch", "origin", self.config["base"]], self.root)
            base = command(["git", "rev-parse", "origin/" + self.config["base"]], self.root)
            already = (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", t["head"], base], cwd=self.root
                ).returncode
                == 0
            )
            if already:
                receipt = {"head": t["head"], "baseBefore": base, "merged": base, "recovered": True}
                self.update(task, landing=encode(receipt))
                return {
                    "summary": "Remote already contains this change; recovered landing",
                    "next": [
                        {
                            "kind": "triage",
                            "purpose": "Assess landed code, review residue and findings",
                        }
                    ],
                }
            # One fresh integration checkout per attempt. Recovery preserves previous checkouts.
            integration = self.store.path / "integrations" / task["attempt"]
            command(["git", "worktree", "add", "--detach", str(integration), base], self.root)
            try:
                command(["git", "merge", "--no-ff", "--no-edit", t["head"]], integration)
            except RuntimeError as e:
                return {
                    "summary": "Integration conflict: " + str(e),
                    "next": [
                        {
                            "kind": "work",
                            "purpose": "Integration conflicted. Resolve against latest base; "
                            "the preserved integration checkout is " + str(integration),
                        }
                    ],
                }
            verification = self.verify(task, integration)
            # Combined verification belongs to the integration, not the candidate head.
            combined = verification
            self.update(task, verification=t["verification"])
            if not combined["ok"]:
                return {
                    "summary": "Combined verification failed: " + combined["output"],
                    "next": [
                        {
                            "kind": "work",
                            "purpose": "Repair combined verification failure: "
                            + combined["output"],
                        }
                    ],
                }
            merged = command(["git", "rev-parse", "HEAD"], integration)
            intent = {
                "candidate": t["head"],
                "base": base,
                "merged": merged,
                "verification": combined,
                "ref": base_ref,
            }
            key = fingerprint([t["head"], base])
            effect = "landing-" + key
            with self.store.transaction() as c:
                self.store.assert_claim(c, task, allow_paused=False)
                c.execute(
                    "INSERT OR IGNORE INTO effects VALUES(?,?,?,?,NULL,?)",
                    (effect, t["id"], "landing", encode(intent), time.time()),
                )
            # --force-with-lease is used ONLY as a compare-and-swap. Both ancestry checks forbid
            # history rewriting: the candidate's exact tested merge includes base and PR head.
            command(["git", "merge-base", "--is-ancestor", base, merged], integration)
            command(["git", "merge-base", "--is-ancestor", t["head"], merged], integration)
            command(
                [
                    "git",
                    "push",
                    "--force-with-lease=" + base_ref + ":" + base,
                    "origin",
                    merged + ":" + base_ref,
                ],
                integration,
            )
            remote_sha = command(["git", "ls-remote", "origin", base_ref], self.root).split()[0]
            if remote_sha != merged:
                raise Conflict("Remote moved after landing; reconcile before completion")
            receipt = {
                "head": t["head"],
                "baseBefore": base,
                "merged": merged,
                "verification": combined,
                "effectId": effect,
            }
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
                c.execute(
                    "UPDATE effects SET receipt=?,updated=? WHERE id=?",
                    (encode(receipt), time.time(), effect),
                )
            self.update(task, landing=encode(receipt), verification=t["verification"])
            return {
                "summary": "Landed the exact verified integration commit",
                "next": [
                    {"kind": "triage", "purpose": "Assess landed code, review residue and findings"}
                ],
            }

    def complete(self, task):
        t = self.gate(task)
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            unresolved = c.execute(
                "SELECT id FROM tasks WHERE track=? AND id<>? AND kind<>'complete' "
                "AND status IN ('queued','running','waiting')",
                (t["id"], task["id"]),
            ).fetchall()
            if unresolved:
                return {
                    "summary": "Completion waits for existing obligations",
                    "wait_for": [r["id"] for r in unresolved],
                }
        if self.config["endpoint"] == "land":
            from .triage import cleared, state_key

            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
                if not cleared(self.store, c, t["id"]):
                    work = self.store.enqueue(
                        c,
                        t["id"],
                        "triage",
                        "Assess untriaged landing before completion",
                        "triage-required:" + state_key(self.store, c, t["id"]),
                    )
                    return {
                        "summary": "Completion waits for post-landing triage",
                        "wait_for": [work],
                    }
            receipt = json.loads(t["landing"]) if t["landing"] else {}
            if receipt.get("head") != t["head"]:
                raise Conflict("Landing evidence missing")
            command(["git", "fetch", "origin", self.config["base"]], self.root)
            command(
                ["git", "merge-base", "--is-ancestor", t["head"], "origin/" + self.config["base"]],
                self.root,
            )
            if self.remote:
                issue = t["issue"]
                self.remote.close_issue(task, issue)

        return {
            "summary": "Goal verified and delivered"
            if self.config["endpoint"] == "land"
            else "Requested review endpoint achieved; landing not performed",
            "adopt_completion": True,
        }

    def execute(self, task):
        stop = threading.Event()

        def pulse():
            while not stop.wait(8):
                try:
                    self.store.heartbeat(task)
                except Conflict:
                    return

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            with file_lock(self.store.path / "locks" / (task["track"] + ".lock")):
                workspace = self.ensure_workspace(task)
                t = self.store.track(task["track"])
                doc = json.loads(t["document"])
                if self.remote and not t["issue"]:
                    issue = self.remote.issue(task, doc)
                    self.update(task, issue=issue["number"])
                if (
                    self.remote
                    and task["kind"] == "work"
                    and task["purpose"].startswith("Repair original obligations after landing")
                ):
                    self.remote.reopen_issue(task, t["issue"])
                if task["kind"] == "land":
                    result = self.land(task)
                elif task["kind"] == "triage":
                    from .triage import Triage

                    Triage(self).run(task)
                    return
                elif task["kind"] == "complete":
                    result = self.complete(task)
                elif task["kind"] == "verify":
                    v = self.verify(task, workspace)
                    result = {
                        "summary": "Verification " + ("passed" if v["ok"] else "failed"),
                        "next": [
                            {
                                "kind": "review" if v["ok"] else "work",
                                "purpose": "Assess current goal" if v["ok"] else v["output"],
                            }
                        ],
                    }
                    if v["ok"]:
                        self.publish(task, workspace, doc)
                else:
                    result = run_worker(
                        self.config,
                        self.context(task, workspace),
                        task,
                        self.store.path,
                        lambda pid: self.store.heartbeat(task, pid),
                    )
                    if result.get("changes"):
                        self.apply_changes(task, workspace, result["changes"])
                    if result.get("verify") or result.get("publish") or result.get("changes"):
                        v = self.verify(task, workspace)
                        if not v["ok"]:
                            result = {
                                "summary": "Verification failed after proposal: " + v["output"],
                                "next": [
                                    {"kind": "work", "purpose": "Fix verification: " + v["output"]}
                                ],
                            }
                        elif result.get("publish"):
                            self.publish(task, workspace, doc)
                    if task["kind"] == "review":
                        self.record_review(task, result)
                    self.record_watches(task, result.get("watches", []))
                adopt = result.pop("adopt_completion", False)
                self.store.finish(task, result)
                if adopt:
                    with self.store.transaction() as c:
                        latest = self.store.track(task["track"], c)
                        if latest["revision"] != task["input_revision"] or latest[
                            "control"
                        ] not in ("active", "paused"):
                            raise Conflict("Completion scope changed")
                        if self.config["endpoint"] == "land":
                            from .triage import cleared

                            if not cleared(self.store, c, task["track"]):
                                raise Conflict("Triage changed before completion adoption")
                        if c.execute(
                            "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
                            (task["track"],),
                        ).fetchone():
                            raise Conflict("New obligations arrived before completion adoption")
                        c.execute(
                            "UPDATE tracks SET status=?,control=?,updated=? WHERE id=?",
                            (
                                "done" if self.config["endpoint"] == "land" else "open",
                                "finished",
                                time.time(),
                                task["track"],
                            ),
                        )
                        self.store.event(
                            c,
                            "completion.adopted",
                            task["track"],
                            {"endpoint": self.config["endpoint"]},
                        )
        except Exception as e:
            self.fail(task, e)
        finally:
            stop.set()
            thread.join(timeout=1)

    def record_watches(self, task, rows):
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            for row in rows:
                if not all(row.get(k) for k in ("observation", "reason", "trigger", "next_action")):
                    raise ValueError("Watch requires observation, reason, trigger, next_action")
                id_ = fingerprint([task["track"], row["observation"]])[:20]
                c.execute(
                    "INSERT OR IGNORE INTO watches VALUES(?,?,?,?,?,?,?)",
                    (id_, task["track"], encode(row), "open", row["trigger"], None, time.time()),
                )

    def fail(self, task, error):
        try:
            self.store.finish(
                task,
                {
                    "summary": str(error),
                    "question": "Execution needs attention: "
                    + str(error)
                    + ". Provide a recovery instruction to resume.",
                },
            )
        except Conflict:
            # Cancellation/reassignment fenced this attempt; its output remains on disk.
            pass
        with self.store.transaction() as c:
            self.store.event(
                c,
                "attempt.error",
                task["track"],
                {"attemptId": task["attempt"], "error": str(error)},
            )

    def reconcile(self):
        with self.store.transaction() as c:
            expired = c.execute(
                "SELECT * FROM tasks WHERE status='running' AND lease<?", (time.time(),)
            ).fetchall()
            for w in expired:
                c.execute(
                    "UPDATE tasks SET status='queued',generation=generation+1,owner=NULL,lease=NULL WHERE id=?",
                    (w["id"],),
                )
                c.execute(
                    "UPDATE attempts SET status='abandoned',finished=? WHERE task=? AND status='running'",
                    (time.time(), w["id"]),
                )
                self.store.event(c, "claim.recovered", w["track"], {"workId": w["id"]})
            c.execute(
                "UPDATE tracks SET control='paused' WHERE control='pause-requested' AND NOT EXISTS "
                "(SELECT 1 FROM tasks WHERE tasks.track=tracks.id AND tasks.status='running')"
            )
            for t in c.execute(
                "SELECT * FROM tracks WHERE control='active' AND status='open'"
            ).fetchall():
                # Semantic coalescing repairs pre-existing duplicate follow-ups too. Keep history.
                for kind in ("review", "land", "triage", "complete", "verify"):
                    pending = c.execute(
                        "SELECT * FROM tasks WHERE track=? AND kind=? AND status IN ('queued','waiting','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,created",
                        (t["id"], kind),
                    ).fetchall()
                    for duplicate in pending[1:]:
                        c.execute(
                            "UPDATE tasks SET status='superseded' WHERE id=? AND status<>'running'",
                            (duplicate["id"],),
                        )
                        c.execute(
                            "UPDATE decisions SET status='superseded',answer=? WHERE task=? AND status='open'",
                            ("Joined " + pending[0]["id"], duplicate["id"]),
                        )
                        self.store.event(
                            c,
                            "work.coalesced",
                            t["id"],
                            {"workId": duplicate["id"], "joined": pending[0]["id"]},
                        )
                # Old completion-only deadlocks are runtime dependency waits, never user decisions.
                for d in c.execute(
                    "SELECT d.* FROM decisions d JOIN tasks w ON w.id=d.task WHERE d.track=? AND d.status='open' AND w.kind='complete' AND w.status='waiting'",
                    (t["id"],),
                ).fetchall():
                    if "Other unfinished obligations remain" in d["question"]:
                        c.execute(
                            "UPDATE decisions SET status='superseded',answer='Runtime dependency reconciliation' WHERE id=?",
                            (d["id"],),
                        )
                        c.execute("UPDATE tasks SET status='queued' WHERE id=?", (d["task"],))
                for waiting in c.execute(
                    "SELECT DISTINCT w.work FROM waits w JOIN tasks t ON t.id=w.work WHERE t.track=? AND t.status='waiting'",
                    (t["id"],),
                ).fetchall():
                    blocked = c.execute(
                        "SELECT 1 FROM waits d JOIN tasks t ON t.id=d.dependency WHERE d.work=? AND t.status NOT IN ('done','superseded','cancelled')",
                        (waiting["work"],),
                    ).fetchone()
                    if not blocked:
                        c.execute("DELETE FROM waits WHERE work=?", (waiting["work"],))
                        c.execute("UPDATE tasks SET status='queued' WHERE id=?", (waiting["work"],))
                busy = c.execute(
                    "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
                    (t["id"],),
                ).fetchone()
                if busy:
                    continue
                last = c.execute(
                    "SELECT r.body FROM results r JOIN tasks w ON w.id=r.task "
                    "WHERE w.track=? ORDER BY r.created DESC LIMIT 1",
                    (t["id"],),
                ).fetchone()
                key = fingerprint(
                    [
                        t["request"],
                        t["revision"],
                        t["head"],
                        t["review"],
                        t["landing"],
                        last[0] if last else None,
                    ]
                )
                existing = c.execute(
                    "SELECT 1 FROM tasks WHERE dedup=?", ("assess:" + key,)
                ).fetchone()
                if existing:
                    # No new facts: ask a concrete recovery question instead of spinning models.
                    id_ = uid("decision")
                    w = self.store.enqueue(
                        c, t["id"], "assess", "No new progress; clarify remaining work", id_
                    )
                    c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (w,))
                    c.execute(
                        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                        (
                            id_,
                            t["id"],
                            w,
                            "No new facts or useful follow-up. What should change?",
                            "open",
                            t["revision"],
                            None,
                            time.time(),
                        ),
                    )
                else:
                    self.store.enqueue(
                        c,
                        t["id"],
                        "assess",
                        "Reconcile remaining obligations and choose useful work",
                        "assess:" + key,
                    )

    def run(self, jobs=2, max_tasks=100, daemon=False):
        owner = uid("driver")
        count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            running = set()
            while count < max_tasks or running:
                self.reconcile()
                while len(running) < jobs and count < max_tasks:
                    task = self.store.claim(owner)
                    if not task:
                        break
                    running.add(pool.submit(self.execute, task))
                    count += 1
                    print(
                        encode(
                            {"claimed": task["id"], "track": task["track"], "kind": task["kind"]}
                        ),
                        flush=True,
                    )
                done = {f for f in running if f.done()}
                for f in done:
                    f.result()
                running -= done
                if not running:
                    snap = self.store.snapshot()
                    active = {t["id"] for t in snap["tracks"] if t["control"] == "active"}
                    pending = any(
                        w["status"] == "queued" and w["track"] in active for w in snap["tasks"]
                    )
                    if pending and count < max_tasks:
                        continue
                    if not daemon or count >= max_tasks:
                        break
                time.sleep(0.25 if running else 2)
        return count
