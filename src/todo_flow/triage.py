"""One disposable post-landing assessment; dispositions and follow-ups commit together."""

import json
import re
import shutil
import subprocess
import time

from .adapters import command, file_lock, files_for_worker
from .store import Conflict, encode, fingerprint
from .worker import run_worker


class InputsChanged(Conflict):
    """Re-read changed evidence without turning an ordinary race into a user decision."""


def state_key(store, c, track):
    t = store.track(track, c)
    rows = {}
    for table in ("findings", "watches"):
        rows[table] = [
            dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE track=? ORDER BY id", (track,))
        ]
    return fingerprint([t["request"], t["revision"], t["head"], t["review"], t["landing"], rows])


def cleared(store, c, track):
    key = state_key(store, c, track)
    return any(
        json.loads(r["body"]).get("cleared")
        for r in c.execute("SELECT body FROM triages WHERE track=? AND input_key=?", (track, key))
    )


class Triage:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store

    def sources(self, c, track):
        t = self.store.track(track, c)
        result = {}
        for f in c.execute("SELECT * FROM findings WHERE track=? AND status='open'", (track,)):
            result[f["id"]] = {"id": f["id"], **json.loads(f["body"])}
        review = json.loads(t["review"]) if t["review"] else {}
        for index, row in enumerate(review.get("additional_assessments", [])):
            id_ = "review-" + fingerprint([review.get("attempt"), index, row])[:20]
            existing = c.execute("SELECT status FROM findings WHERE id=?", (id_,)).fetchone()
            if not existing or existing[0] == "open":
                result[id_] = {
                    "id": id_,
                    "observation": row.get("id", "Review observation"),
                    "evidence": row.get("evidence", ""),
                    "verdict": row.get("verdict"),
                    "origin": "review",
                }
        for w in c.execute("SELECT * FROM watches WHERE track=? AND status='open'", (track,)):
            id_ = "watch-" + w["id"]
            result[id_] = {
                "id": id_,
                **json.loads(w["body"]),
                "origin": "watch",
                "watch_id": w["id"],
            }
        return list(result.values())

    def candidates(self, doc, sources, terms=None):
        # Ordinary filesystem grep; the worker can refine it without waking a person.
        terms = (
            terms
            or list(
                dict.fromkeys(
                    re.findall(
                        r"[\w-]{3,}",
                        " ".join(
                            [doc["title"], doc["goal"]]
                            + [s.get("observation", "") for s in sources]
                        ),
                    )
                )
            )[:30]
        )
        found = set()
        if terms and shutil.which("rg"):
            # Only tracks/<id>/track.* is a candidate. Rich-document revision and
            # asset folders can contain the same filenames but are not track IDs.
            args = [
                "rg",
                "--max-depth",
                "2",
                "-l",
                "-i",
                "-F",
                "-g",
                "track.html",
                "-g",
                "track.md",
            ]
            for term in terms:
                args += ["-e", term]
            completed = subprocess.run(
                args + [str(self.store.path / "tracks")], capture_output=True, text=True
            )
            if completed.returncode not in (0, 1):
                raise RuntimeError("Filesystem duplicate search failed: " + completed.stderr)
            found = {p.rsplit("/", 2)[-2] for p in completed.stdout.splitlines()}
        if terms and not shutil.which("rg"):
            for path in (self.store.path / "tracks").glob("*/track.*"):
                if path.suffix in (".md", ".html") and any(
                    term.casefold() in path.read_text().casefold() for term in terms
                ):
                    found.add(path.parent.name)
        candidates = []
        with self.store.connect() as c:
            for id_ in sorted(found - {doc["id"]}):
                t = self.store.track(id_, c)
                d = json.loads(t["document"])
                candidates.append(
                    {
                        "id": id_,
                        "title": d["title"],
                        "goal": d["goal"],
                        "scope": d["scope"],
                        "status": t["status"],
                        "updated": t["updated"],
                    }
                )
        return {
            "terms": terms,
            "candidates": candidates[:40],
            "matches": len(candidates),
            "truncated": len(candidates) > 40,
        }

    def remote_sources(self, track):
        if not self.engine.remote or not track["pr"]:
            return []
        result = []
        review = json.loads(track["review"])
        own_review = review.get("receipt", {}).get("id")
        repo = self.engine.config["github"]
        for category, path in [
            ("review", f"pulls/{track['pr']}/reviews"),
            ("inline", f"pulls/{track['pr']}/comments"),
            ("discussion", f"issues/{track['pr']}/comments"),
        ]:
            pages = json.loads(
                command(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/{path}?per_page=100"])
            )
            for row in (r for page in pages for r in page):
                if not row.get("body") or (category == "review" and row["id"] == own_review):
                    continue
                id_ = "remote-" + fingerprint([category, row["id"], row["body"]])[:20]
                with self.store.connect() as c:
                    old = c.execute("SELECT status FROM findings WHERE id=?", (id_,)).fetchone()
                if old and old[0] != "open":
                    continue
                result.append(
                    {
                        "id": id_,
                        "origin": "github-" + category,
                        "observation": row["body"],
                        "evidence": row.get("html_url", ""),
                        "path": row.get("path"),
                        "commit": row.get("commit_id"),
                    }
                )
        return result

    def prepare(self, task):
        t = self.engine.gate(task)
        if not t["landing"]:
            raise Conflict("Triage requires confirmed landing")
        with file_lock(self.store.path / "locks/git-metadata.lock", blocking=True):
            command(["git", "fetch", "origin", self.engine.config["base"]], self.engine.root)
            base = command(
                ["git", "rev-parse", "origin/" + self.engine.config["base"]], self.engine.root
            )
            command(["git", "merge-base", "--is-ancestor", t["head"], base], self.engine.root)
            checkout = self.store.path / "triage-checkouts" / task["attempt"]
            if not checkout.exists():
                command(
                    ["git", "worktree", "add", "--detach", str(checkout), base], self.engine.root
                )
        with self.store.connect() as c:
            sources = self.sources(c, t["id"])
            key = state_key(self.store, c, t["id"])
        sources.extend(self.remote_sources(t))
        terms = None
        if task["purpose"].startswith("Search related tracks: "):
            terms = json.loads(task["purpose"].split(": ", 1)[1])
        context = self.engine.context(task, checkout)
        context["files"] = files_for_worker(checkout, self.engine.config["context_patterns"])
        context["diff"] = command(
            ["git", "diff", json.loads(t["landing"])["baseBefore"] + ".." + base], self.engine.root
        )[-100000:]
        context["triage_context"] = {
            "base": base,
            "input_key": key,
            "sources": sources,
            "duplicate_search": self.candidates(context["document"], sources, terms),
        }
        return context

    def run(self, task):
        context = self.prepare(task)
        result = run_worker(
            self.engine.config,
            context,
            task,
            self.store.path,
            lambda pid: self.store.heartbeat(task, pid),
        )
        if result.get("triage_search"):
            terms = result["triage_search"]
            if terms == context["triage_context"]["duplicate_search"]["terms"]:
                self.store.finish(
                    task,
                    {
                        "summary": "Duplicate search did not narrow the candidates",
                        "question": "Which existing scope should receive this ambiguous finding?",
                    },
                )
            else:
                self.store.finish(
                    task,
                    {
                        "summary": "Refine filesystem duplicate search",
                        "next": [
                            {
                                "kind": "triage",
                                "purpose": "Search related tracks: "
                                + json.dumps(terms, ensure_ascii=False),
                            }
                        ],
                    },
                )
            return
        if result.get("question"):
            self.store.finish(task, result)
            return
        # Don't apply judgments against a base that moved while the agent read it.
        base = command(
            ["git", "ls-remote", "origin", "refs/heads/" + self.engine.config["base"]],
            self.engine.root,
        ).split()[0]
        if base != context["triage_context"]["base"]:
            self.store.finish(
                task,
                {
                    "summary": "Base advanced; refresh only the post-landing assessment",
                    "next": [{"kind": "triage", "purpose": "Reassess changed landed code"}],
                },
            )
            return
        try:
            self.apply(task, context, result)
        except InputsChanged:
            self.store.finish(
                task,
                {
                    "summary": "Triage evidence changed; reread only this assessment",
                    "next": [
                        {"kind": "triage", "purpose": "Reassess changed findings or target tracks"}
                    ],
                },
            )

    def apply(self, task, context, result):
        items = result.get("triage")
        if not isinstance(items, list):
            raise ValueError(
                "Triage worker must return triage dispositions, including an empty list when nothing remains"
            )
        sources = {s["id"]: s for s in context["triage_context"]["sources"]}
        ids = [x["source"] for x in items]
        if len(ids) != len(set(ids)) or not set(sources) <= set(ids):
            raise ValueError("Every triage source needs exactly one disposition")
        if any(id_ not in sources and not id_.startswith("new:") for id_ in ids):
            raise ValueError("New observations require a stable new: key")
        # Validate the whole proposal before performing any local mutation.
        for item in items:
            if not all(
                item.get(k)
                for k in ("source", "action", "observation", "evidence", "reason", "scope")
            ):
                raise ValueError(
                    "Disposition needs source, action, observation, evidence, reason and scope"
                )
            if item["action"] not in (
                "repair",
                "existing",
                "new-track",
                "watch",
                "resolved",
                "dismissed",
            ):
                raise ValueError("Unknown disposition")
            if item["scope"] not in ("in-scope", "out-of-scope", "uncertain"):
                raise ValueError("Unknown finding scope")
            if item["scope"] == "in-scope" and item["action"] in ("existing", "new-track", "watch"):
                raise Conflict("Original obligations cannot be moved to a follow-up or Watch")
            if item["action"] == "repair" and item["scope"] != "in-scope":
                raise Conflict("Repair must stay inside the selected scope")
            if item["action"] == "watch" and item.get("confirmed") is not False:
                raise ValueError("Confirmed defects need work or a TODO, not Watch")
        receipt_id = (
            "triage-"
            + fingerprint([task["track"], context["triage_context"]["input_key"], task["id"]])[:24]
        )
        repairs = []
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            if state_key(self.store, c, task["track"]) != context["triage_context"]["input_key"]:
                raise InputsChanged("Triage inputs changed; preserve proposal and reassess")
            for item in items:
                source_id = item["source"]
                finding_id = (
                    source_id
                    if source_id in sources
                    else "finding-" + fingerprint([task["track"], source_id])[:20]
                )
                source = sources.get(source_id, {"id": finding_id, "origin": "triage"})
                target = None
                if item["action"] == "repair":
                    repairs.append(
                        item["observation"] + "\n" + item["evidence"] + "\n" + item["reason"]
                    )
                elif item["action"] == "existing":
                    target = item.get("target")
                    t = self.store.track(target, c)
                    observed = next(
                        (
                            x
                            for x in context["triage_context"]["duplicate_search"]["candidates"]
                            if x["id"] == target
                        ),
                        None,
                    )
                    if observed and observed.get("updated") != t["updated"]:
                        raise InputsChanged("Target track changed during triage")
                    if target == task["track"] or t["status"] == "done":
                        raise Conflict(
                            "Existing follow-up must be a different unfinished track; regressions need a linked new track"
                        )
                    self.store.event(
                        c,
                        "finding.linked",
                        target,
                        {
                            "sourceTrack": task["track"],
                            "finding": finding_id,
                            "observation": item["observation"],
                            "evidence": item["evidence"],
                        },
                    )
                    c.execute("UPDATE tracks SET updated=? WHERE id=?", (time.time(), target))
                    if t["control"] == "active":
                        self.store.enqueue(
                            c,
                            target,
                            "assess",
                            "Triage finding within this track scope: "
                            + item["observation"]
                            + "\n"
                            + item["evidence"],
                            "linked:" + finding_id,
                        )
                elif item["action"] == "new-track":
                    if context["triage_context"]["duplicate_search"].get("truncated"):
                        raise ValueError(
                            "Refine the filesystem duplicate search before registering new work"
                        )
                    raw = item.get("registration")
                    doc = json.loads(raw) if isinstance(raw, str) else None
                    if not isinstance(doc, dict):
                        raise ValueError("New TODO requires a complete JSON registration document")
                    # IDs are supplied by the author; collisions are surfaced, never overwritten.
                    doc["derivedFrom"] = list(
                        dict.fromkeys([*doc.get("derivedFrom", []), task["track"]])
                    )
                    doc["triageSource"] = finding_id
                    doc["duplicateCheck"] = {
                        "terms": context["triage_context"]["duplicate_search"]["terms"],
                        "reason": item["reason"],
                    }
                    doc["documentReview"] = "pending-human-review"
                    doc.setdefault("language", context.get("language", "en"))
                    existing = c.execute(
                        "SELECT document FROM tracks WHERE id=?", (doc["id"],)
                    ).fetchone()
                    if existing and existing[0] != encode(doc):
                        raise InputsChanged(
                            "Another author registered this ID; reassess absorption"
                        )
                    self.store.register(doc, connection=c)
                    target = doc["id"]
                    if target == task["track"]:
                        raise ValueError("Follow-up must not replace its source")
                    self.store.event(
                        c,
                        "triage.todo-registered",
                        target,
                        {
                            "sourceTrack": task["track"],
                            "finding": finding_id,
                            "requiresSelection": True,
                        },
                    )
                elif item["action"] == "watch":
                    if not item.get("trigger") or not item.get("next_action"):
                        raise ValueError("Watch needs a concrete trigger and next action")
                    watch_id = (
                        source.get("watch_id")
                        or fingerprint([task["track"], item["observation"]])[:20]
                    )
                    body = {k: item[k] for k in ("observation", "reason", "trigger", "next_action")}
                    body["lastAssessment"] = {
                        "evidence": item["evidence"],
                        "base": context["triage_context"]["base"],
                    }
                    c.execute(
                        "INSERT INTO watches VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,trigger_key=excluded.trigger_key",
                        (
                            watch_id,
                            task["track"],
                            encode(body),
                            "open",
                            item["trigger"],
                            None,
                            time.time(),
                        ),
                    )
                if source.get("watch_id") and item["action"] not in ("watch", "repair"):
                    w = c.execute(
                        "SELECT body FROM watches WHERE id=?", (source["watch_id"],)
                    ).fetchone()
                    body = json.loads(w[0])
                    body["disposition"] = {
                        "evidence": item["evidence"],
                        "target": target,
                        "action": item["action"],
                    }
                    status = (
                        "promoted"
                        if target
                        else "resolved"
                        if item["action"] == "resolved"
                        else "dismissed"
                    )
                    c.execute(
                        "UPDATE watches SET status=?,body=? WHERE id=?",
                        (status, encode(body), source["watch_id"]),
                    )
                item["target"] = target
                body = {**source, **item, "id": finding_id, "target": target, "receipt": receipt_id}
                c.execute(
                    "INSERT INTO findings VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,status=excluded.status,updated=excluded.updated",
                    (
                        finding_id,
                        task["track"],
                        encode(body),
                        "open" if item["action"] == "repair" else "disposed",
                        time.time(),
                    ),
                )
            if repairs:
                t = self.store.track(task["track"], c)
                self.store.event(
                    c,
                    "delivery.repair-required",
                    task["track"],
                    {
                        "previousPR": t["pr"],
                        "previousLanding": json.loads(t["landing"]),
                        "triage": receipt_id,
                    },
                )
                branch = "todo/" + task["track"] + "-repair-" + receipt_id[-10:]
                workspace = str(self.store.path / "worktrees" / branch.replace("/", "-"))
                c.execute(
                    "UPDATE tracks SET branch=?,workspace=?,pr=NULL,head=NULL,verification=NULL,review=NULL,landing=NULL,updated=? WHERE id=?",
                    (branch, workspace, time.time(), task["track"]),
                )
            follow = (
                {
                    "kind": "work",
                    "purpose": "Repair original obligations after landing:\n"
                    + "\n\n".join(repairs),
                }
                if repairs
                else {"kind": "complete", "purpose": "Confirm triaged landing and close issue"}
            )
            receipt = {
                "summary": result["summary"],
                "items": items,
                "base": context["triage_context"]["base"],
                "candidate": context["head"],
                "documentRevision": task["input_revision"],
                "cleared": not repairs,
                "attempt": task["attempt"],
            }
            c.execute(
                "INSERT INTO triages VALUES(?,?,?,?,?)",
                (
                    receipt_id,
                    task["track"],
                    state_key(self.store, c, task["track"]),
                    encode(receipt),
                    time.time(),
                ),
            )
            self.store.event(
                c,
                "triage.recorded",
                task["track"],
                {"triage": receipt_id, "summary": result["summary"], "cleared": not repairs},
            )
            self.store.finish(
                task,
                {"summary": result["summary"], "triage_receipt": receipt_id, "next": [follow]},
                connection=c,
            )
