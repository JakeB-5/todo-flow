import argparse
import json
import re
import sys
from pathlib import Path

from .adapters import command
from .engine import Engine
from .language import select_language
from .release import VERSION, CONTRACTS, project_compatibility
from .maintenance import runtime_guard
from .store import Conflict, Store, encode, fingerprint


def initialize(args):
    repo = Path(args.repo).resolve()
    command(["git", "rev-parse", "--verify", "HEAD"], repo)
    if args.github and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.github):
        raise ValueError("github must be owner/repository")
    verify = json.loads(args.verify)
    if not isinstance(verify, list) or not verify or not all(isinstance(x, str) for x in verify):
        raise ValueError("--verify must be a nonempty JSON argv array")
    if args.endpoint == "land" and not args.allow_land:
        raise ValueError("land endpoint requires --allow-land")
    writable = args.write or ["*.py", "tests/*.py"]
    config = {
        "repo": str(repo),
        "github": args.github,
        "base": args.base,
        "verify": verify,
        "worker": {"type": "command", "argv": json.loads(args.worker_command)}
        if args.worker_command
        else {"type": args.worker, "model": args.model},
        "writable_patterns": writable,
        "context_patterns": args.context or writable + ["README.md"],
        "endpoint": args.endpoint,
        "allow_land": args.allow_land,
        "worker_timeout": args.worker_timeout,
        "verify_timeout": args.verify_timeout,
        "language": select_language(args.language, interactive=sys.stdin.isatty()),
        "schema_version": 1,
        "worker_protocol": 1,
        "created_by": VERSION,
        "min_engine_version": "0.0.1",
    }
    store = Store(args.state or repo / "todo")
    store.configure(config)
    print(encode({"state": str(store.path), "config": config}))


def parser():
    p = argparse.ArgumentParser(prog="todo-flow")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--state", help="Project state directory (default: todo)")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init")
    i.add_argument("--repo", required=True)
    i.add_argument("--github")
    i.add_argument("--base", default="main")
    i.add_argument("--verify", required=True, help="JSON command argv, never a shell string")
    i.add_argument("--write", action="append")
    i.add_argument("--context", action="append")
    i.add_argument("--model", default=None)
    i.add_argument("--worker", choices=["claude", "codex"], default="claude")
    i.add_argument(
        "--worker-command", help="Trusted adapter argv JSON; stdin context, stdout result JSON"
    )
    i.add_argument("--endpoint", choices=["review", "land"], default="review")
    i.add_argument("--allow-land", action="store_true")
    i.add_argument("--worker-timeout", type=int, default=600)
    i.add_argument("--verify-timeout", type=int, default=180)
    i.add_argument(
        "--language",
        choices=["en", "ko"],
        help="Primary language; prompts in a terminal, otherwise defaults to en",
    )
    r = sub.add_parser("register", help="Agent-only track registration; no dashboard authoring")
    r.add_argument("file")
    r.add_argument("--assets", help="Snapshot this directory as the document assets/ bundle")
    r.add_argument("--expected-revision", type=int)
    migration = sub.add_parser(
        "migrate-files", help="Copy legacy SQLite state into a new filesystem store"
    )
    migration.add_argument("--source", required=True)
    migration.add_argument("--target", required=True)
    tr = sub.add_parser("trackrun", help="Request selected tracks and run headless workers")
    tr.add_argument("--version", action="version", version=f"trackrun {VERSION}")
    tr.add_argument("tracks", nargs="+")
    tr.add_argument("--jobs", type=int, default=2)
    tr.add_argument("--max-tasks", type=int, default=100)
    tr.add_argument("--request-only", action="store_true")
    tr.add_argument("--request-id")
    st = sub.add_parser("start")
    st.add_argument("tracks", nargs="+")
    st.add_argument("--request-id")
    for name in ["pause", "resume", "cancel"]:
        c = sub.add_parser(name)
        c.add_argument("track")
    a = sub.add_parser("answer")
    a.add_argument("decision")
    a.add_argument("--text", required=True)
    run = sub.add_parser("run")
    run.add_argument("--jobs", type=int, default=2)
    run.add_argument("--max-tasks", type=int, default=100)
    run.add_argument("--daemon", action="store_true")
    sub.add_parser("status")
    sub.add_parser("picks")
    sub.add_parser("doctor")
    sub.add_parser("reconcile")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    signal = sub.add_parser("signal")
    signal.add_argument("trigger")
    signal.add_argument("--version", required=True)
    sub.add_parser("watches")
    wd = sub.add_parser("watch-dispose")
    wd.add_argument("id")
    wd.add_argument("--status", choices=["resolved", "dismissed", "promoted"], required=True)
    wd.add_argument("--evidence", required=True)
    wd.add_argument("--target")
    install = sub.add_parser("install-skills")
    install.add_argument("--target", required=True)
    install.add_argument(
        "--language",
        choices=["en", "ko"],
        help="Use the project language, or choose one for standalone skills",
    )
    update = sub.add_parser("update-skills", help="Plan/apply a safe project skill update")
    update.add_argument("--target", required=True)
    update.add_argument("--dry-run", action="store_true")
    update.add_argument(
        "--adopt", action="store_true", help="Adopt legacy skills only when they match this bundle"
    )
    restore = update.add_mutually_exclusive_group()
    restore.add_argument("--rollback", metavar="BACKUP_ID")
    restore.add_argument("--recover", action="store_true")
    check = sub.add_parser("compatibility", help="Inspect engine and project format compatibility")
    check.add_argument("--target", help="Also check this installed skill bundle")
    upgrade = sub.add_parser(
        "upgrade", help="Guarded uv tool update from an explicit local release wheel"
    )
    choice = upgrade.add_mutually_exclusive_group(required=True)
    choice.add_argument("--wheel")
    choice.add_argument("--rollback", metavar="BACKUP_ID")
    choice.add_argument("--recover", action="store_true")
    upgrade.add_argument("--dry-run", action="store_true")
    sub.add_parser("hooks")
    clean = sub.add_parser("cleanup")
    clean.add_argument("track")
    return p


def dispatch(args):
    try:
        if args.command == "migrate-files":
            from .migrate import migrate

            print(encode(migrate(args.source, args.target)))
            return
        if args.command == "init":
            initialize(args)
            return
        if args.command in ("install-skills", "update-skills"):
            from . import skill_updates

            state, language = skill_context(args)
            if args.command == "update-skills" and (args.rollback or args.recover):
                if args.dry_run or args.adopt:
                    raise ValueError(
                        "Recovery/rollback cannot be combined with --dry-run or --adopt"
                    )
                result = skill_updates.restore(args.target, state, args.rollback)
            else:
                result = skill_updates.update(
                    args.target,
                    state,
                    language,
                    install=args.command == "install-skills",
                    dry_run=getattr(args, "dry_run", False),
                    adopt=getattr(args, "adopt", False),
                )
            print(encode(result))
            return
        store = Store(args.state or Path.cwd() / "todo")
        store.config()
        result = None
        if args.command == "register":
            from .documents import load

            document = load(args.file, args.assets)
            if not document.get("presentation") and "language" not in document:
                with store.connect() as connection:
                    existing = connection.execute(
                        "SELECT document FROM tracks WHERE id=?", (document.get("id"),)
                    ).fetchone()
                # Preserve legacy registration identity and the existing document language.
                language = (
                    json.loads(existing[0]).get("language")
                    if existing
                    else store.config().get("language", "en")
                )
                if language:
                    document["language"] = language
            result = store.register(document, args.expected_revision)
            result["document"] = str(store.path / "tracks" / document["id"] / "track.html")
        elif args.command in ("start", "trackrun"):
            if args.command == "trackrun" and (args.jobs < 1 or args.max_tasks < 1):
                raise ValueError("jobs and max-tasks must be positive")
            result = {}
            for track in args.tracks:
                try:
                    result[track] = store.start(track, args.request_id)
                except (ValueError, Conflict) as e:
                    result[track] = {"error": str(e)}
            if args.command == "trackrun" and not args.request_only:
                print(encode({"requests": result}), flush=True)
                result = {"tasks": Engine(store).run(args.jobs, args.max_tasks)}
        elif args.command in ("pause", "resume", "cancel"):
            result = store.control(args.track, args.command)
        elif args.command == "answer":
            store.answer(args.decision, args.text)
            result = {"answered": args.decision}
        elif args.command == "run":
            if args.jobs < 1 or args.max_tasks < 1:
                raise ValueError("jobs and max-tasks must be positive")
            result = {"tasks": Engine(store).run(args.jobs, args.max_tasks, args.daemon)}
        elif args.command == "status":
            result = store.snapshot()
        elif args.command == "picks":
            result = [
                {
                    "id": t["id"],
                    "title": json.loads(t["document"])["title"],
                    "control": t["control"],
                    "revision": t["revision"],
                    "selectable": t["status"] != "done"
                    and t["control"] not in ("active", "paused", "pause-requested"),
                }
                for t in store.snapshot()["tracks"]
            ]
        elif args.command == "reconcile":
            Engine(store).reconcile()
            result = {"reconciled": True}
        elif args.command == "doctor":
            config = store.config()
            checks = {
                "git": command(["git", "status", "--porcelain"], config["repo"]),
                "origin": command(["git", "remote", "get-url", "origin"], config["repo"]),
            }
            if config["github"]:
                checks["github"] = command(
                    ["gh", "repo", "view", config["github"], "--json", "nameWithOwner"]
                )
            result = checks
        elif args.command == "serve":
            from .web import serve

            serve(store, args.port)
            return
        elif args.command == "hooks":
            result = {
                "internal": [
                    "document.registered",
                    "execution.accepted",
                    "worker.claimed",
                    "work.result",
                    "verification.recorded",
                    "effect.confirmed",
                    "decision.answered",
                    "execution.control",
                    "claim.recovered",
                    "watch.triggered",
                    "watch.disposed",
                    "completion.adopted",
                    "triage.recorded",
                    "triage.todo-registered",
                    "finding.linked",
                    "delivery.repair-required",
                ],
                "delivery": "Filesystem redo journal + durable agenda; rebuild query cache on restart",
                "externalCallbacks": False,
            }
        elif args.command == "watch-dispose":
            if not args.evidence.strip():
                raise ValueError("Disposition needs evidence")
            with store.transaction() as c:
                row = c.execute("SELECT * FROM watches WHERE id=?", (args.id,)).fetchone()
                if not row or row["status"] != "open":
                    raise Conflict("Watch is not open")
                if args.status == "promoted":
                    if not args.target:
                        raise ValueError("Promotion requires --target TRACK_ID")
                    store.track(args.target, c)
                body = json.loads(row["body"])
                body["disposition"] = {
                    "status": args.status,
                    "evidence": args.evidence,
                    "target": args.target,
                }
                c.execute(
                    "UPDATE watches SET status=?,body=? WHERE id=?",
                    (args.status, encode(body), args.id),
                )
                store.event(c, "watch.disposed", row["track"], body["disposition"])
                result = {"id": args.id, "status": args.status}
        elif args.command == "watches":
            result = store.snapshot()["watches"]
        elif args.command == "signal":
            with store.transaction() as c:
                rows = c.execute(
                    "SELECT * FROM watches WHERE status='open' AND trigger_key=?", (args.trigger,)
                ).fetchall()
                result = []
                for w in rows:
                    if w["last_signal"] == args.version:
                        continue
                    t = store.track(w["track"], c)
                    # Closed goals don't silently start new execution. Signal is visible for selection.
                    if t["control"] == "active":
                        id_ = store.enqueue(
                            c,
                            w["track"],
                            "watch",
                            "Reassess watch: " + w["body"],
                            fingerprint([w["id"], args.version]),
                        )
                        result.append(id_)
                    store.event(
                        c,
                        "watch.triggered",
                        w["track"],
                        {
                            "watchId": w["id"],
                            "version": args.version,
                            "needsSelection": t["control"] != "active",
                        },
                    )
                    c.execute(
                        "UPDATE watches SET last_signal=? WHERE id=?", (args.version, w["id"])
                    )
        elif args.command == "cleanup":
            from .adapters import file_lock

            t = store.track(args.track)
            if t["control"] != "finished":
                raise Conflict("Only finished executions may be cleaned up")
            with file_lock(store.path / "locks" / (args.track + ".lock")):
                workspace = t["workspace"]
                if workspace and Path(workspace).exists():
                    if command(
                        ["git", "status", "--porcelain", "--untracked-files=all"], workspace
                    ):
                        raise Conflict("Uncommitted files remain; cleanup refused")
                    if store.config()["endpoint"] != "land":
                        raise Conflict("Unlanded work is preserved")
                    command(
                        ["git", "fetch", "origin", store.config()["base"]], store.config()["repo"]
                    )
                    command(
                        [
                            "git",
                            "merge-base",
                            "--is-ancestor",
                            t["head"],
                            "origin/" + store.config()["base"],
                        ],
                        store.config()["repo"],
                    )
                    command(["git", "worktree", "remove", workspace], store.config()["repo"])
                result = {"cleaned": args.track, "branchPreserved": t["branch"]}
        print(encode(result))
    except (ValueError, Conflict, RuntimeError, OSError) as e:
        print(encode({"error": str(e)}), file=sys.stderr)
        raise SystemExit(2) from e


def skill_context(args):
    target = Path(args.target).resolve()
    contexts = [json.loads(p.read_text()) for p in target.glob("*/project.json")]
    inferred_states = {c["state"] for c in contexts if c.get("state")}
    if args.state:
        state = Path(args.state).resolve()
        if inferred_states and inferred_states != {str(state)}:
            raise ValueError("Installed skills belong to another state directory")
    elif len(inferred_states) == 1:
        state = Path(next(iter(inferred_states))).resolve()
    elif inferred_states:
        raise ValueError("Skill directory mixes projects; specify separate installation targets")
    else:
        project = (
            target.parent.parent if target.parent.name in (".agents", ".claude") else Path.cwd()
        )
        state = (project / "todo").resolve()
    config_file = state / "config/1.json"
    config = Store(state).config() if config_file.exists() else None
    requested = getattr(args, "language", None)
    inherited = (
        config.get("language", "en")
        if config
        else contexts[0].get("language", "en")
        if contexts
        else None
    )
    if requested and inherited and requested != inherited:
        raise ValueError("Skill language must match the initialized project language")
    language = select_language(
        inherited or requested, interactive=args.command == "install-skills" and sys.stdin.isatty()
    )
    return state, language


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "upgrade":
            from .engine_updates import launch

            if args.dry_run and not args.wheel:
                raise ValueError("--dry-run requires --wheel")
            print(
                encode(
                    launch(
                        args.wheel,
                        dry_run=args.dry_run,
                        restore=args.rollback,
                        recover=args.recover,
                    )
                )
            )
            return
        if args.command in ("install-skills", "update-skills"):
            # The skill operation takes the exclusive project lease after resolving its target.
            with runtime_guard():
                dispatch(args)
            return
        state = Path(
            args.state
            or (Path(args.repo) / "todo" if args.command == "init" else Path.cwd() / "todo")
        ).resolve()
        with runtime_guard(state):
            if args.command == "compatibility":
                result = {
                    "engine": VERSION,
                    "contracts": CONTRACTS,
                    "project": project_compatibility(state),
                }
                if args.target:
                    from .skill_updates import plan

                    result["skills"] = plan(args.target)[0]
                print(encode(result))
                if not result["project"]["compatible"] or result.get("skills", {}).get("conflicts"):
                    raise SystemExit(2)
                return
            dispatch(args)
    except (ValueError, Conflict, RuntimeError, OSError) as error:
        print(encode({"error": str(error)}), file=sys.stderr)
        raise SystemExit(2) from error


def trackrun():
    # Keep --state before the subcommand for the common project override.
    args = sys.argv[1:]
    prefix = []
    if "--state" in args:
        i = args.index("--state")
        prefix, args = args[i : i + 2], args[:i] + args[i + 2 :]
    main(prefix + ["trackrun"] + args)


if __name__ == "__main__":
    main()
