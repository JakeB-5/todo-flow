"""Create a public fixture and prove independent overlapping workers finish without intervention."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from live_smoke import run


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--create-public", required=True, metavar="OWNER/NEW-REPO")
    p.add_argument("--root", required=True)
    p.add_argument("--worker", choices=["claude", "codex"], default="claude")
    p.add_argument(
        "--launcher", choices=["auto", "headless", "orca", "tmux", "terminal"], default="auto"
    )
    p.add_argument(
        "--register-orca",
        action="store_true",
        help="Register this disposable fixture in the running Orca app",
    )
    p.add_argument(
        "--exercise-triage",
        action="store_true",
        help="Seed explicit residual observations and verify automatic dispositions",
    )
    p.add_argument("--language", choices=["en", "ko"], default="en")
    args = p.parse_args()
    root = Path(args.root).resolve()
    if root.exists():
        p.error("root must not already exist")
    repo = root / "fixture"
    repo.mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n.todo-flow/\n")
    (repo / "README.md").write_text(
        "# Parallel TODO Flow acceptance\n\nDisposable public fixture. No private source.\n"
    )
    (repo / "test_baseline.py").write_text(
        "import unittest\nclass Baseline(unittest.TestCase):\n    def test_fixture(self):\n        self.assertTrue(True)\n"
    )
    (repo / "context_fixture.py").write_text(
        "# Large unrelated fixture: explore only the files needed for the selected task.\n" * 4000
    )
    run(["git", "init", "-b", "main"], repo)
    run(["git", "add", "."], repo)
    run(["git", "commit", "-m", "Create parallel acceptance fixture"], repo)
    run(
        [
            "gh",
            "repo",
            "create",
            args.create_public,
            "--public",
            "--source",
            str(repo),
            "--remote",
            "origin",
            "--push",
        ]
    )
    state = root / "state"
    if args.register_orca:
        from todo_flow.launchers import orca_command

        print(run([orca_command(), "repo", "add", "--path", str(repo), "--json"]), flush=True)
    cli = [sys.executable, "-m", "todo_flow", "--state", str(state)]
    run(
        cli
        + [
            "init",
            "--language",
            args.language,
            "--repo",
            str(repo),
            "--github",
            args.create_public,
            "--base",
            "main",
            "--verify",
            json.dumps([sys.executable, "-m", "unittest", "discover", "-v"]),
            "--write",
            "*.py",
            "--context",
            "*.py",
            "--context",
            "README.md",
            "--endpoint",
            "land",
            "--allow-land",
            "--worker",
            args.worker,
            "--launcher",
            args.launcher,
        ]
    )
    specs = [
        (
            "compact-whitespace",
            "Compact whitespace",
            "text_utils.py",
            "compact(text)",
            "Collapse every whitespace run to one ASCII space and trim edges; whitespace-only input becomes empty.",
            [
                "Mixed spaces, tabs and newlines collapse correctly.",
                "Empty and whitespace-only strings return empty.",
            ],
        ),
        (
            "ordered-unique",
            "Preserve first distinct values",
            "sequence_utils.py",
            "ordered_unique(values)",
            "Return distinct hashable values in first-occurrence order, without changing input.",
            [
                "Repeated values are removed in stable order.",
                "Empty input and already unique inputs work; input is unchanged.",
            ],
        ),
        (
            "safe-divide",
            "Divide with an explicit fallback",
            "number_utils.py",
            "safe_divide(numerator, denominator, fallback=None)",
            "Return numerator / denominator except when denominator == 0, return fallback. Do not suppress other errors.",
            [
                "Nonzero division works for positive and negative values.",
                "Zero denominator returns provided fallback, default None.",
            ],
        ),
    ]
    for id_, title, file, signature, goal, conditions in specs:
        doc = {
            "id": id_,
            "title": title,
            "goal": f"Add {signature} in {file}. {goal}",
            "scope": f"Only create {file} and test_{file}. Do not modify other files.",
            "evidence": "Public parallel acceptance test specification.",
            "conditions": [
                {"id": f"c{i + 1}", "text": text, "method": "Meaningful unittest cases."}
                for i, text in enumerate(conditions)
            ],
        }
        from todo_flow.documents import render_html

        docpath = root / (id_ + ".html")
        docpath.write_text(render_html(doc))
        run(cli + ["register", str(docpath)])
    if args.exercise_triage:
        from todo_flow.store import Store, encode
        import time

        seed = Store(state)
        seed.register(
            {
                "id": "license-documentation",
                "title": "Document the fixture licensing policy",
                "goal": "State the project's licensing policy in README.md",
                "scope": "README.md license policy section only",
                "evidence": "Public fixture intentionally omits licensing instructions",
                "conditions": [
                    {
                        "id": "policy",
                        "text": "README contains a licensing policy section",
                        "method": "Read the document",
                    }
                ],
            }
        )
        with seed.transaction() as c:
            for id_, observation in [
                (
                    "usage-docs",
                    "Confirmed out-of-scope residual: README.md has no instructions for users to import compact, ordered_unique or safe_divide, and no test command. The three selected tracks only implement their function and tests. Register a distinct documentation TODO unless an existing candidate covers these usage instructions.",
                ),
                (
                    "license-docs",
                    "Confirmed out-of-scope residual: README.md does not state a licensing policy. The existing license-documentation track already owns this exact separate scope. Link this finding to it; do not choose the policy or start that track.",
                ),
            ]:
                c.execute(
                    "INSERT INTO findings VALUES(?,?,?,?,?)",
                    (
                        id_,
                        "compact-whitespace",
                        encode(
                            {
                                "observation": observation,
                                "evidence": "README.md contains only a public-fixture heading and one description sentence; compare with the existing track documents",
                                "origin": "acceptance-fixture",
                            }
                        ),
                        "open",
                        time.time(),
                    ),
                )
            seed.event(
                c,
                "test.residuals-seeded",
                "compact-whitespace",
                {"summary": "Predeclared acceptance observations; not injected during execution"},
            )
    subprocess.run(
        cli + ["trackrun", *[x[0] for x in specs], "--jobs", "3", "--max-tasks", "40"], check=True
    )
    snapshot = json.loads(run(cli + ["status"]))
    prs = json.loads(
        run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                args.create_public,
                "--state",
                "all",
                "--json",
                "number,state,url,mergedAt,headRefOid",
            ]
        )
    )
    issues = json.loads(
        run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                args.create_public,
                "--state",
                "all",
                "--json",
                "number,state,url",
            ]
        )
    )
    from todo_flow.store import Store

    with Store(state).connect() as db:
        intervals = [
            dict(r)
            for r in db.execute(
                "SELECT a.id,a.started,a.finished,w.track,w.kind FROM attempts a JOIN tasks w ON w.id=a.task ORDER BY a.started"
            )
        ]
    overlap = [
        {
            "a": a["id"],
            "b": b["id"],
            "tracks": [a["track"], b["track"]],
            "kind": a["kind"],
            "otherKind": b["kind"],
        }
        for i, a in enumerate(intervals)
        for b in intervals[i + 1 :]
        if a["track"] != b["track"]
        and a["finished"]
        and b["finished"]
        and max(a["started"], b["started"]) < min(a["finished"], b["finished"])
    ]
    questions = snapshot["decisions"]
    errors = [x for x in snapshot["events"] if x["type"] == "attempt.error"]
    tracks = [
        {k: t[k] for k in ("id", "status", "control", "issue", "pr", "head")}
        for t in snapshot["tracks"]
        if t["id"] in {x[0] for x in specs}
    ]
    reviews = {}
    for t in tracks:
        reviews[t["id"]] = (
            json.loads(run(["gh", "api", f"repos/{args.create_public}/pulls/{t['pr']}/reviews"]))
            if t["pr"]
            else []
        )
    success = (
        all(t["status"] == "done" and t["control"] == "finished" for t in tracks)
        and not questions
        and not errors
        and len(prs) == 3
        and all(x["state"] == "MERGED" for x in prs)
        and len(issues) == 3
        and all(x["state"] == "CLOSED" for x in issues)
        and any(x["kind"] == "work" and x["otherKind"] == "work" for x in overlap)
        and all(any(r["commit_id"] == t["head"] for r in reviews[t["id"]]) for t in tracks)
    )
    triages = [
        {"id": r["id"], "track": r["track"], **json.loads(r["body"])} for r in snapshot["triages"]
    ]
    followups = [
        {k: t[k] for k in ("id", "status", "control", "request")}
        for t in snapshot["tracks"]
        if t["id"] not in {x[0] for x in specs}
    ]
    success = success and all(
        any(r["track"] == t["id"] and r["cleared"] and r["candidate"] == t["head"] for r in triages)
        for t in tracks
    )
    if args.exercise_triage:
        actions = [item["action"] for r in triages for item in r["items"]]
        success = (
            success
            and "new-track" in actions
            and "existing" in actions
            and all(t["control"] == "idle" and t["request"] is None for t in followups)
        )
    run(["git", "pull", "--ff-only"], repo)
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-v"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    success = success and tests.returncode == 0
    launches = [
        json.loads(path.read_text()) for path in sorted((state / "attempts").glob("*/launch.json"))
    ]
    terminal_exits = [
        json.loads(path.read_text())
        for path in sorted((state / "attempts").glob("*/terminal-process.json"))
    ]
    if args.launcher == "orca":
        success = (
            success
            and bool(launches)
            and all(item["backend"] == "orca" and item["status"] == "accepted" for item in launches)
            and len(terminal_exits) == len(launches)
            and all(
                item["status"] == "exited" and item["returncode"] == 0 for item in terminal_exits
            )
        )
    report = {
        "repository": "https://github.com/" + args.create_public,
        "worker": args.worker,
        "launcher": args.launcher,
        "terminalLaunches": launches,
        "terminalExits": terminal_exits,
        "contextFixtureBytes": (repo / "context_fixture.py").stat().st_size,
        "largestWorkerInputBytes": max(
            path.stat().st_size for path in (state / "attempts").glob("*/input.json")
        ),
        "success": success,
        "tracks": tracks,
        "triages": triages,
        "followups": followups,
        "prs": prs,
        "issues": issues,
        "overlap": overlap,
        "attempts": intervals,
        "decisions": questions,
        "errors": errors,
        "reviews": {
            k: [
                {"id": r["id"], "state": r["state"], "commit": r["commit_id"], "url": r["html_url"]}
                for r in v
            ]
            for k, v in reviews.items()
        },
        "finalTests": {"exitCode": tests.returncode, "output": tests.stdout + tests.stderr},
        "state": str(state),
    }
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "success": success,
                "report": str(root / "report.json"),
                "prs": prs,
                "overlappingAttempts": len(overlap),
            },
            indent=2,
        ),
        flush=True,
    )
    if not success:
        raise SystemExit(
            "Parallel acceptance incomplete. Preserved state; do not present manual recovery as an unattended pass."
        )


if __name__ == "__main__":
    main()
