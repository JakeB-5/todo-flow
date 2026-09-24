"""External effects have durable intent/receipt pairs; credentials stay in gh."""

import contextlib
import fcntl
import fnmatch
import json
import os
import subprocess
from pathlib import Path

from .store import Conflict, encode, fingerprint


def command(argv, cwd=None, input=None, timeout=120, include_stderr=False):
    proc = subprocess.run(
        argv,
        cwd=cwd,
        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode:
        raise RuntimeError(
            f"{argv[0]} failed ({proc.returncode}): {proc.stderr[-3000:]} {proc.stdout[-1000:]}"
        )
    return (proc.stdout + (proc.stderr if include_stderr else "")).strip()


@contextlib.contextmanager
def file_lock(path, blocking=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as e:
            raise Conflict("Resource is still owned by another runtime") from e
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class GitHub:
    def __init__(self, store):
        self.store = store
        self.config = store.config()
        self.repo = self.config["github"]
        self.root = Path(self.config["repo"])

    def api(self, path, method="GET", body=None):
        args = ["gh", "api", f"repos/{self.repo}/{path}", "--method", method]
        if body is not None:
            args += ["--input", "-"]
        output = command(args, input=encode(body) if body is not None else None)
        return json.loads(output) if output else {}

    def effect(self, task, kind, key, intent, fn):
        id_ = fingerprint([self.repo, task["track"], kind, key])
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            row = c.execute("SELECT * FROM effects WHERE id=?", (id_,)).fetchone()
            if row and row["receipt"]:
                return json.loads(row["receipt"])
            c.execute(
                "INSERT OR IGNORE INTO effects VALUES(?,?,?,?,NULL,?)",
                (id_, task["track"], kind, encode(intent), __import__("time").time()),
            )
        # fn must reconcile the remote before mutating; a lost response is never blindly replayed.
        receipt = fn(id_)
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            c.execute(
                "UPDATE effects SET receipt=?,updated=? WHERE id=?",
                (encode(receipt), __import__("time").time(), id_),
            )
            self.store.event(
                c,
                "effect.confirmed",
                task["track"],
                {"kind": kind, "effectId": id_, "receipt": receipt},
            )
        return receipt

    def issue(self, task, doc):
        def reconcile(id_):
            marker = f"<!-- todo-flow:{id_} -->"
            # All pages: do not lose deduplication once the repository has many issues.
            pages = command(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{self.repo}/issues?state=all&per_page=100",
                ]
            )
            found = [
                x
                for page in json.loads(pages)
                for x in page
                if marker in (x.get("body") or "") and "pull_request" not in x
            ]
            if found:
                return {"number": found[0]["number"], "url": found[0]["html_url"]}
            body = doc["goal"] + "\n\n" + doc["scope"] + "\n\nAcceptance:\n"
            body += "\n".join("- " + x["text"] for x in doc["conditions"]) + "\n\n" + marker
            x = self.api("issues", "POST", {"title": doc["title"], "body": body})
            return {"number": x["number"], "url": x["html_url"]}

        return self.effect(
            task, "issue", [doc["id"], task["input_revision"]], {"track": doc["id"]}, reconcile
        )

    def pr(self, task, track, doc):
        def reconcile(id_):
            prs = self.api(
                "pulls?state=all&head=" + self.repo.split("/")[0] + ":" + track["branch"]
            )
            if prs:
                x = prs[0]
            else:
                x = self.api(
                    "pulls",
                    "POST",
                    {
                        "title": doc["title"],
                        "head": track["branch"],
                        "base": self.config["base"],
                        "body": f"{doc['goal']}\n\nRelated to #{track['issue']}\n\n"
                        f"Verification: `{encode(self.config['verify'])}`\n\n"
                        f"<!-- todo-flow:{id_} -->",
                    },
                )
            return {"number": x["number"], "url": x["html_url"]}

        return self.effect(task, "pr", track["branch"], {"head": track["head"]}, reconcile)

    def post_review(self, task, track, result):
        def reconcile(id_):
            marker = f"<!-- todo-flow:{id_} -->"
            pages = command(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{self.repo}/pulls/{track['pr']}/reviews?per_page=100",
                ]
            )
            found = [
                x for page in json.loads(pages) for x in page if marker in (x.get("body") or "")
            ]
            if found:
                return {"id": found[0]["id"], "url": found[0]["html_url"]}
            # The repository owner cannot APPROVE their own PR. Record an independent agent's
            # full assessment as COMMENT, never misrepresent it as a GitHub approval.
            body = (
                "Independent agent review (fresh read-only session; GitHub COMMENT)\n\n"
                + encode(result)
                + "\n\n"
                + marker
            )
            x = self.api(
                f"pulls/{track['pr']}/reviews",
                "POST",
                {"commit_id": track["head"], "body": body, "event": "COMMENT"},
            )
            return {"id": x["id"], "url": x["html_url"]}

        return self.effect(task, "review", task["attempt"], {"head": track["head"]}, reconcile)

    def reopen_issue(self, task, number):
        def reconcile(_):
            current = self.api(f"issues/{number}")
            if current["state"] != "open":
                current = self.api(f"issues/{number}", "PATCH", {"state": "open"})
            return {"number": number, "state": current["state"], "url": current["html_url"]}

        return self.effect(
            task,
            "reopen-issue",
            [number, self.store.track(task["track"])["branch"]],
            {"issue": number},
            reconcile,
        )

    def close_issue(self, task, number):
        def reconcile(_):
            current = self.api(f"issues/{number}")
            if current["state"] != "closed":
                try:
                    current = self.api(f"issues/{number}", "PATCH", {"state": "closed"})
                except RuntimeError:
                    # GitHub's merge processor may close the same issue concurrently.
                    current = self.api(f"issues/{number}")
                    if current["state"] != "closed":
                        raise
            return {"number": number, "state": current["state"], "url": current["html_url"]}

        track = self.store.track(task["track"])
        return self.effect(
            task,
            "close-issue",
            [number, track["branch"], track["head"]],
            {"issue": number},
            reconcile,
        )


def permitted(path, patterns):
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or not p.parts or any(x.startswith(".") for x in p.parts):
        return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
