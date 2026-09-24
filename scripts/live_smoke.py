"""Explicit public-safe GitHub smoke. Creates a NEW disposable repository and runs real agents."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--create-public", required=True, metavar="OWNER/NEW-REPO")
    p.add_argument("--root", required=True, help="New empty local directory")
    p.add_argument("--model")
    args = p.parse_args()
    root = Path(args.root).resolve()
    if root.exists():
        p.error("--root must not exist; existing data is never overwritten")
    root.mkdir(parents=True)
    repo = root / "fixture"
    repo.mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.todo-flow/\n")
    (repo / "README.md").write_text(
        "# TODO Flow public smoke\n\nDisposable fixture, no private source.\n"
    )
    (repo / "greeting.py").write_text('def greet(name):\n    return "Hello, " + name\n')
    run(["git", "init", "-b", "main"], repo)
    run(["git", "add", "."], repo)
    run(["git", "commit", "-m", "Create disposable fixture"], repo)
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
    cli = [sys.executable, "-m", "todo_flow", "--state", str(state)]
    init = cli + [
        "init",
        "--repo",
        str(repo),
        "--github",
        args.create_public,
        "--verify",
        json.dumps([sys.executable, "-m", "unittest", "discover", "-v"]),
        "--write",
        "*.py",
        "--context",
        "*.py",
        "--endpoint",
        "land",
        "--allow-land",
    ]
    if args.model:
        init += ["--model", args.model]
    run(init)
    doc = {
        "id": "greeting-trim",
        "title": "Normalize greeting name",
        "goal": "greet(name) trims surrounding whitespace and uses world for an empty name.",
        "scope": "greeting.py and a new test_greeting.py.",
        "evidence": "Smoke fixture requirement.",
        "conditions": [
            {"id": "trim", "text": 'greet(" Ada ") == "Hello, Ada"', "method": "Unit test"},
            {"id": "empty", "text": 'greet("  ") == "Hello, world"', "method": "Unit test"},
        ],
    }
    file = root / "track.json"
    file.write_text(json.dumps(doc))
    print(run(cli + ["register", str(file)]))
    print(run(cli + ["start", doc["id"]]))
    subprocess.run(cli + ["run", "--jobs", "1", "--max-tasks", "20"], check=True)
    snapshot = json.loads(run(cli + ["status"]))
    t = snapshot["tracks"][0]
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
                "number,state,url,mergedAt",
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
    report = {
        "track": {k: t[k] for k in ("id", "status", "control", "issue", "pr", "head")},
        "prs": prs,
        "issues": issues,
        "state": str(state),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if t["status"] != "done" or not any(x["state"] == "MERGED" for x in prs):
        raise SystemExit(
            "Smoke incomplete: inspect status/decisions; data and worktrees are preserved."
        )


if __name__ == "__main__":
    main()
