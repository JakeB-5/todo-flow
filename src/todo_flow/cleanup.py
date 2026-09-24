"""Remove owned, delivered run resources while retaining durable evidence and branches."""

import json
import os
from pathlib import Path
import subprocess
import time

from .adapters import command, file_lock
from .launchers import orca_result
from .maintenance import guarded, write_json
from .store import Conflict, fingerprint


def receipt_path(store, track):
    key = fingerprint([track["request"], track["revision"], track["head"]])
    return store.path / "cleanup" / track["id"] / (key + ".json")


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def resources(store, track, snapshot):
    task_ids = {row["id"] for row in snapshot["tasks"] if row["track"] == track["id"]}
    attempts = [row for row in snapshot["attempts"] if row["task"] in task_ids]
    paths = {track["workspace"]} if track["workspace"] else set()
    for attempt in attempts:
        folder = store.path / "attempts" / attempt["id"]
        for group in ("integrations", "triage-checkouts"):
            paths.add(str(store.path / group / attempt["id"]))
        source = folder / "input.json"
        if source.exists():
            raw = source.read_text()
            if "\nTASK CONTEXT:\n" in raw:
                raw = raw.split("\nTASK CONTEXT:\n", 1)[1]
            try:
                context = json.loads(raw)
            except ValueError:
                context = {}
            if context.get("workspace"):
                paths.add(context["workspace"])
        for path in (folder / "terminal-spec.json",):
            spec = read_json(path)
            if spec.get("cwd"):
                paths.add(spec["cwd"])
    return sorted(paths), attempts


def registered_worktrees(repo):
    output = command(["git", "worktree", "list", "--porcelain", "-z"], repo)
    return {
        str(Path(part.removeprefix("worktree ")).resolve())
        for part in output.split("\0")
        if part.startswith("worktree ")
    }


def owned_path(store, value):
    path = Path(value)
    roots = [store.path / name for name in ("worktrees", "integrations", "triage-checkouts")]
    return any(
        path.parent == root
        and not root.is_symlink()
        and not path.is_symlink()
        and path.resolve().parent == root.resolve()
        for root in roots
    )


def check_finished(store, original, connection):
    current = store.track(original["id"], connection)
    if current["control"] != "finished" or any(
        current[key] != original[key] for key in ("request", "revision", "head")
    ):
        raise Conflict("Execution changed during cleanup")
    if connection.execute(
        "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
        (original["id"],),
    ).fetchone():
        raise Conflict("Unfinished work remains; cleanup deferred")


def terminal_cleanup(folder, dry_run):
    launch = read_json(folder / "launch.json")
    backend = launch.get("backend")
    if not backend or backend == "headless":
        return None
    receipt_file = folder / "terminal-process.json"
    receipt = read_json(receipt_file)
    item = {"attempt": folder.name, "backend": backend, "status": "preserved"}
    if receipt.get("status") != "exited" or type(receipt.get("returncode")) is not int:
        return {**item, "reason": "Worker exit is unconfirmed; inspect its logs"}
    if backend == "orca":
        recorded = launch["terminal"]
        if not all(recorded.get(key) for key in ("ptyId", "incarnationId", "worktreeId")):
            return {**item, "reason": "Terminal ownership metadata is incomplete"}
        inventory = orca_result(
            launch["cli"],
            [
                "terminal",
                "list",
                "--worktree",
                launch["worktree"],
            ],
            launch["repo"],
        )["terminals"]
        current = next((t for t in inventory if t.get("ptyId") == recorded.get("ptyId")), None)
        if current is None:
            return {**item, "status": "absent"}
        if any(current.get(k) != recorded.get(k) for k in ("incarnationId", "worktreeId", "title")):
            return {**item, "reason": "Terminal identity or title changed; possible user reuse"}
        finished = receipt.get("finished_at", receipt_file.stat().st_mtime)
        if not isinstance(current.get("lastOutputAt"), (int, float)):
            return {**item, "reason": "Terminal activity cannot be verified"}
        if current["lastOutputAt"] > (finished + 2) * 1000:
            return {
                **item,
                "reason": "Terminal has output after worker completion; possible user reuse",
            }
        marker = f"TODO Flow worker exited: {receipt['returncode']}"
        preview = current.get("preview", "")
        tail = preview.rsplit(marker, 1)[-1].strip()
        if (
            marker not in preview
            or current.get("agentIdentity")
            or (tail and ("\n" in tail or not tail.endswith(("%", "$", "#", ">", "❯"))))
        ):
            return {**item, "reason": "Terminal is not an identifiable idle worker shell"}
        item["handle"] = current["handle"]
        if not dry_run:
            closed = orca_result(
                launch["cli"],
                [
                    "terminal",
                    "close",
                    "--terminal",
                    current["handle"],
                ],
                launch["repo"],
            )
            if not closed.get("close", {}).get("ptyKilled"):
                raise RuntimeError("Orca did not confirm terminal shutdown")
        return {**item, "status": "would-close" if dry_run else "closed"}
    if backend == "tmux":
        if not launch.get("socket"):
            return {**item, "reason": "Legacy tmux server identity is unknown; preserve the window"}
        tmux = ["tmux", "-S", launch["socket"]]
        handle = launch.get("handle", "")
        windows = command(tmux + ["list-windows", "-a", "-F", "#{window_id}"], launch.get("repo"))
        if handle not in windows.splitlines():
            return {**item, "status": "absent"}
        title = command(tmux + ["display-message", "-p", "-t", handle, "#{window_name}"])
        panes = command(tmux + ["list-panes", "-t", handle, "-F", "#{pane_dead}"])
        if title != launch["title"] or panes != "1":
            return {**item, "reason": "Terminal was reused or its exit is not confirmed"}
        if not dry_run:
            command(tmux + ["kill-window", "-t", handle])
        return {**item, "status": "would-close" if dry_run else "closed"}
    return {**item, "reason": "Custom terminal has no supported close receipt; close it manually"}


@guarded
def cleanup_track(store, track_id, dry_run=False):
    config = store.config()
    with file_lock(store.path / "locks" / (track_id + ".lock")):
        track = store.track(track_id)
        with store.connect() as connection:
            check_finished(store, track, connection)
        if not dry_run:
            with store.transaction() as connection:
                check_finished(store, track, connection)
                store.event(
                    connection,
                    "cleanup.requested",
                    track_id,
                    {"request": track["request"], "head": track["head"]},
                )
        destination = receipt_path(store, track)
        previous = read_json(destination)
        paths, attempts = resources(store, track, store.snapshot())
        report = {
            "track": track_id,
            "request": track["request"],
            "head": track["head"],
            "dryRun": dry_run,
            "worktrees": [],
            "terminals": [],
            "branchesPreserved": True,
            "status": "pending",
        }
        # Abandoned workers may still be alive even though a later attempt finished the track.
        for attempt in attempts:
            if attempt["status"] != "finished" and attempt.get("pid"):
                try:
                    os.kill(attempt["pid"], 0)
                except ProcessLookupError:
                    continue
                raise Conflict("An earlier worker may still be running; cleanup deferred")
        old_terminals = {r["attempt"]: r for r in previous.get("terminals", [])}
        for attempt in attempts:
            prior = old_terminals.get(attempt["id"], {})
            if prior.get("status") in ("closed", "absent"):
                report["terminals"].append(prior)
                continue
            pending = {"attempt": attempt["id"], "status": "pending"}
            report["terminals"].append(pending)
            if not dry_run:
                write_json(destination, report)
            try:
                with store.transaction() as connection:
                    check_finished(store, track, connection)
                    item = terminal_cleanup(store.path / "attempts" / attempt["id"], dry_run)
            except (
                OSError,
                ValueError,
                KeyError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as error:
                item = {"attempt": attempt["id"], "status": "preserved", "reason": str(error)}
            if item:
                report["terminals"][-1] = item
            else:
                report["terminals"].pop()
            if not dry_run:
                write_json(destination, report)
        terminal_wait = any(t["status"] == "preserved" for t in report["terminals"])
        old_paths = {r["path"]: r for r in previous.get("worktrees", [])}
        # Match the runtime's lock order: track -> landing -> Git metadata -> state.
        with (
            file_lock(store.path / "locks/landing.lock", blocking=True),
            file_lock(store.path / "locks/git-metadata.lock", blocking=True),
        ):
            known = registered_worktrees(config["repo"])
            common = (
                Path(config["repo"])
                / command(["git", "rev-parse", "--git-common-dir"], config["repo"])
            ).resolve()
            remote_error = None
            try:
                if config["endpoint"] == "land":
                    command(["git", "fetch", "origin", config["base"]], config["repo"])
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                remote_error = str(error)
            for value in paths:
                path = Path(value)
                if not path.exists() and str(path.resolve()) not in known:
                    if value in old_paths:
                        old = old_paths[value]
                        report["worktrees"].append(
                            old
                            if old["status"] == "removed"
                            else {"path": value, "status": "absent"}
                        )
                    continue
                item = {"path": value, "status": "preserved"}
                report["worktrees"].append(item)
                try:
                    with store.transaction() as connection:
                        check_finished(store, track, connection)
                        if not owned_path(store, value) or str(path.resolve()) not in known:
                            raise Conflict("Path is not an owned, registered runtime worktree")
                        if (
                            path / command(["git", "rev-parse", "--git-common-dir"], path)
                        ).resolve() != common:
                            raise Conflict("Worktree repository identity changed")
                        if config["endpoint"] != "land" or not track["landing"]:
                            raise Conflict("Unlanded review candidates are preserved")
                        if terminal_wait:
                            raise Conflict(
                                "A terminal still needs inspection; preserve its checkout"
                            )
                        if remote_error:
                            raise Conflict("Cannot confirm remote inclusion: " + remote_error)
                        if command(["git", "status", "--porcelain", "--untracked-files=all"], path):
                            raise Conflict("Uncommitted or untracked files remain")
                        ignored = command(
                            [
                                "git",
                                "ls-files",
                                "--others",
                                "--ignored",
                                "--exclude-standard",
                                "-z",
                            ],
                            path,
                        )
                        if any(
                            "__pycache__" not in Path(p).parts or Path(p).suffix != ".pyc"
                            for p in ignored.split("\0")
                            if p
                        ):
                            raise Conflict(
                                "Ignored user files remain (only Python bytecode caches are disposable)"
                            )
                        head = command(["git", "rev-parse", "HEAD"], path)
                        command(
                            [
                                "git",
                                "merge-base",
                                "--is-ancestor",
                                head,
                                "origin/" + config["base"],
                            ],
                            config["repo"],
                        )
                        item["head"] = head
                        if not dry_run:
                            item["status"] = "removing"
                            write_json(destination, report)
                            command(["git", "worktree", "remove", value], config["repo"])
                        item["status"] = "would-remove" if dry_run else "removed"
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    item["status"] = "preserved"
                    item["reason"] = str(error)
                if not dry_run:
                    write_json(destination, report)
        report["status"] = (
            "deferred"
            if any(r["status"] == "preserved" for r in report["worktrees"] + report["terminals"])
            else "complete"
        )
        report["at"] = time.time()
        if not dry_run:
            write_json(destination, report)
            with store.transaction() as connection:
                store.event(
                    connection,
                    "cleanup." + report["status"],
                    track_id,
                    {
                        "request": track["request"],
                        "receipt": str(destination),
                        "status": report["status"],
                    },
                )
        return report
