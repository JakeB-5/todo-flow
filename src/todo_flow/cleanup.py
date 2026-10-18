"""Remove owned, delivered run resources while retaining durable evidence and branches."""

import json
import os
from pathlib import Path
import subprocess
import time

from . import clean_integration, obsolete_integration, repositories
from .adapters import command, file_lock
from .cleanup_native import native_terminal_cleanup
from .cleanup_orca import OrcaCleanup, owner as orca_owner
from .launchers import orca_result
from .maintenance import guarded, write_json
from .process_barrier import ProcessBarrier
from .store import Conflict, fingerprint
from .terminal_release import retire_launch
from .triage import cleared
from .verification_artifacts import checkout_identity, reclaim


def receipt_path(store, track):
    key = fingerprint(
        [
            track["request"],
            track["revision"],
            track["head"],
            *repositories.identity_components(track),
            track.get("landing"),
        ]
    )
    return store.path / "cleanup" / track["id"] / (key + ".json")


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def resources(store, track, snapshot, config=None):
    task_ids = {row["id"] for row in snapshot["tasks"] if row["track"] == track["id"]}
    attempts = [row for row in snapshot["attempts"] if row["task"] in task_ids]
    paths = {track["workspace"]} if track["workspace"] else set()
    if config and repositories.multi(config):
        for state in repositories.all_states(track, config).values():
            if state["workspace"]:
                paths.add(state["workspace"])
    for attempt in attempts:
        folder = store.path / "attempts" / attempt["id"]
        for group in ("integrations", "triage-checkouts"):
            paths.add(str(store.path / group / attempt["id"]))
            if config and repositories.multi(config):
                paths.update(
                    str(store.path / group / (attempt["id"] + "-" + row["name"]))
                    for row in repositories.specs(config)
                )
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
            for repository in (context.get("repositories") or {}).values():
                if isinstance(repository, dict) and repository.get("workspace"):
                    paths.add(repository["workspace"])
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


def common_dir(repo):
    value = Path(command(["git", "rev-parse", "--git-common-dir"], repo))
    return (value if value.is_absolute() else Path(repo) / value).resolve()


def owned_path(store, value, config):
    path = Path(value)
    roots = [
        Path(config.get("worktree_root") or store.path / "worktrees"),
        store.path / "integrations",
        store.path / "triage-checkouts",
    ]
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
        current[key] != original[key]
        for key in (
            "request",
            "revision",
            "head",
            "workspace",
            "branch",
            "repo_state",
            "review",
            "landing",
        )
    ):
        raise Conflict("Execution changed during cleanup")
    if connection.execute(
        "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
        (original["id"],),
    ).fetchone():
        raise Conflict("Unfinished work remains; cleanup deferred")


def confirmed_landing(store, track, connection, config):
    """Consume the existing landing contract, independently of today's endpoint."""
    check_finished(store, track, connection)
    review = json.loads(track["review"]) if track["review"] else {}
    landing = json.loads(track["landing"]) if track["landing"] else {}
    if repositories.multi(config):
        heads = repositories.head_map(track, config)
        declarations = repositories.specs(config)
        records = landing.get("repositories")
        if (
            review.get("verdict") != "met"
            or review.get("head") != track["head"]
            or review.get("documentRevision") != track["revision"]
            or review.get("heads") != heads
            or landing.get("head") != track["head"]
            or landing.get("heads") != heads
            or landing.get("status") != "complete"
            or not isinstance(records, dict)
            or set(records) != set(heads)
        ):
            raise Conflict("Every repository needs current independent review and landing")
        for spec in declarations:
            name = spec["name"]
            row = records[name]
            if row.get("head") != heads[name] or not row.get("merged"):
                raise Conflict("Repository landing receipt does not match candidate: " + name)
            if row.get("recovered") is True:
                if row.get("baseBefore") != row.get("merged"):
                    raise Conflict("Recovered repository landing anchors do not match: " + name)
                continue
            verification = row.get("verification", {})
            effect = connection.execute(
                "SELECT track,kind,intent,receipt FROM effects WHERE id=?",
                (row.get("effectId"),),
            ).fetchone()
            if (
                verification.get("ok") is not True
                or verification.get("head") != row["merged"]
                or effect is None
                or effect["track"] != track["id"]
                or effect["kind"] != "landing"
                or not effect["receipt"]
                or json.loads(effect["receipt"]) != row
            ):
                raise Conflict("Repository landing effect or verification does not match: " + name)
            intent = json.loads(effect["intent"])
            if (
                intent.get("repository") != name
                or intent.get("candidate") != heads[name]
                or intent.get("merged") != row["merged"]
                or intent.get("base") != row.get("baseBefore")
                or intent.get("verification") != verification
            ):
                raise Conflict("Repository landing intent does not match receipt: " + name)
        if not cleared(store, connection, track["id"]):
            raise Conflict("Current cleared post-landing triage is missing")
        return landing
    if (
        review.get("verdict") != "met"
        or review.get("head") != track["head"]
        or review.get("documentRevision") != track["revision"]
    ):
        raise Conflict("Current independent review is missing or not met")
    if landing.get("head") != track["head"] or not landing.get("merged"):
        raise Conflict("Unlanded or mismatched landing receipt; preserve the candidate")
    if landing.get("recovered") is True:
        if landing.get("baseBefore") != landing["merged"]:
            raise Conflict("Recovered landing anchors do not match")
    else:
        verification = landing.get("verification", {})
        effect = connection.execute(
            "SELECT track,kind,intent,receipt FROM effects WHERE id=?",
            (landing.get("effectId"),),
        ).fetchone()
        if (
            verification.get("ok") is not True
            or verification.get("head") != landing["merged"]
            or effect is None
            or effect["track"] != track["id"]
            or effect["kind"] != "landing"
            or not effect["receipt"]
            or json.loads(effect["receipt"]) != landing
        ):
            raise Conflict("Landing effect or combined verification does not match")
        intent = json.loads(effect["intent"])
        if (
            intent.get("candidate") != track["head"]
            or intent.get("merged") != landing["merged"]
            or intent.get("base") != landing.get("baseBefore")
            or intent.get("verification") != verification
        ):
            raise Conflict("Landing intent does not match its receipt")
    if not cleared(store, connection, track["id"]):
        raise Conflict("Current cleared post-landing triage is missing")
    return landing


def local_target(path):
    return {
        "checkout": checkout_identity(path),
        "head": command(["git", "rev-parse", "HEAD"], path),
        "branch": command(["git", "rev-parse", "--symbolic-full-name", "HEAD"], path),
    }


def terminal_cleanup(folder, dry_run, previous=None):
    launch = read_json(folder / "launch.json")
    if launch.get("execution_mode") == "orca-native":
        return native_terminal_cleanup(folder, launch, previous)
    if previous and previous.get("execution_mode") == "orca-native":
        return {
            **previous,
            "status": "preserved",
            "reason": "Native launch identity changed since cleanup began",
        }
    if previous and previous.get("status") in ("closed", "absent"):
        return previous
    if "owner" in launch or "terminal_slot" in launch:
        return retire_launch(folder, dry_run=dry_run)
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
        ProcessBarrier(store.path, track_id).require_clear()
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
        paths, attempts = resources(store, track, store.snapshot(), config)
        delivery_key = fingerprint(
            [track[key] for key in ("request", "revision", "head")]
            + repositories.identity_components(track)
            + [track[key] for key in ("review", "landing")]
        )
        report = {
            "track": track_id,
            "request": track["request"],
            "head": track["head"],
            "dryRun": dry_run,
            "delivery": delivery_key,
            "landing": {"status": "unconfirmed", "head": track["head"]},
            # Preserve every deletion intent even if recovery stops during terminal cleanup.
            "worktrees": [dict(row) for row in previous.get("worktrees", [])],
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
            pending = {"attempt": attempt["id"], "status": "pending"}
            report["terminals"].append(pending)
            if not dry_run:
                write_json(destination, report)
            try:
                with store.transaction() as connection:
                    check_finished(store, track, connection)
                    item = terminal_cleanup(
                        store.path / "attempts" / attempt["id"], dry_run, previous=prior
                    )
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
        old_paths = {r["path"]: r for r in report["worktrees"]}
        paths = sorted(set(paths) | set(old_paths))
        # Match the runtime's lock order: track -> landing -> Git metadata -> state.
        with (
            file_lock(store.path / "locks/landing.lock", blocking=True),
            file_lock(store.path / "locks/git-metadata.lock", blocking=True),
        ):
            declarations = repositories.specs(config)
            roots = {row["name"]: Path(row["path"]).resolve() for row in declarations}
            commons = {name: common_dir(root) for name, root in roots.items()}
            common_to_repo = {str(value): name for name, value in commons.items()}
            known_by_path = {}
            for name, root in roots.items():
                known_by_path.update({path: name for path in registered_worktrees(root)})
            known = set(known_by_path)
            workspace_state = {}
            repository_states = repositories.all_states(track, config)
            for name, state in repository_states.items():
                if state["workspace"]:
                    workspace_state[str(Path(state["workspace"]).resolve())] = (name, state)
            for value, item in old_paths.items():
                checkout = (item.get("target") or {}).get("checkout", {})
                name = common_to_repo.get(str(Path(checkout.get("common", "")).resolve()))
                if name:
                    known_by_path[str(Path(value).resolve())] = name
            delivery_error = None
            remote_head = None
            remote_heads = {}
            try:
                if previous.get("delivery", delivery_key) != delivery_key:
                    raise Conflict("Delivery evidence changed since cleanup began")
                with store.connect() as connection:
                    landing = confirmed_landing(store, track, connection, config)
                if repositories.multi(config):
                    confirmed = {}
                    for spec in declarations:
                        name, root = spec["name"], roots[spec["name"]]
                        row = landing["repositories"][name]
                        head = repositories.fetch_base(root, spec["base"], runner=command)
                        command(
                            ["git", "merge-base", "--is-ancestor", row["head"], row["merged"]], root
                        )
                        command(["git", "merge-base", "--is-ancestor", row["merged"], head], root)
                        confirmed[name] = {**row, "remoteHead": head}
                        remote_heads[name] = head
                    report["landing"].update(status="confirmed", repositories=confirmed)
                else:
                    root = roots[declarations[0]["name"]]
                    remote_head = repositories.fetch_base(root, config["base"], runner=command)
                    command(
                        ["git", "merge-base", "--is-ancestor", track["head"], landing["merged"]],
                        root,
                    )
                    command(
                        ["git", "merge-base", "--is-ancestor", landing["merged"], remote_head],
                        root,
                    )
                    report["landing"].update(
                        status="confirmed", merged=landing["merged"], remoteHead=remote_head
                    )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as error:
                delivery_error = str(error)
                report["landing"]["reason"] = delivery_error
            for value in paths:
                path = Path(value)
                name = known_by_path.get(str(path.resolve()))
                if name is None:
                    state = workspace_state.get(str(path.resolve()))
                    name = state[0] if state else declarations[0]["name"]
                repo_root = roots[name]
                repo_common = commons[name]
                repo_name = name if repositories.multi(config) else None
                candidate_head = repository_states[name]["head"]
                repo_remote_head = remote_heads.get(name, remote_head)
                exists = os.path.lexists(path)
                registered = str(path.resolve()) in known
                prior = old_paths.get(value)
                if not exists and not registered and prior is None and value != track["workspace"]:
                    continue
                item = prior if prior is not None else {"path": value}
                if prior is None:
                    report["worktrees"].append(item)
                previous_status = item.get("status")
                item["status"] = "preserved"
                try:
                    with store.transaction() as connection:
                        check_finished(store, track, connection)
                        ProcessBarrier(store.path, track_id).require_clear()
                        if delivery_error:
                            raise Conflict(delivery_error)
                        confirmed_landing(store, track, connection, config)
                        evidence = orca_owner(
                            store, track, value, str(repo_root), connection=connection
                        )
                        if evidence is None and item.get("orca"):
                            raise Conflict("Orca cleanup ownership is missing or changed")
                        if evidence is None and not owned_path(store, value, config):
                            raise Conflict("Path is not an owned runtime worktree")
                        if terminal_wait:
                            raise Conflict(
                                "A terminal still needs inspection; preserve its checkout"
                            )
                        orca = (
                            OrcaCleanup(
                                store,
                                track,
                                str(repo_root),
                                value,
                                item,
                                evidence,
                                connection=connection,
                            )
                            if evidence is not None
                            else None
                        )
                        if item.get("retirement") and not exists and not registered:
                            clean_integration.check(
                                store,
                                track,
                                path,
                                item["target"],
                                item["retirement"],
                                repo_remote_head,
                                connection,
                                candidate_head=candidate_head,
                                repo_name=repo_name,
                            )
                        if orca is not None:
                            if not orca.inspect():
                                orca.branches(absent=True, dry_run=dry_run)
                                item["status"] = (
                                    "removed" if item.get("removalIntent") else "absent"
                                )
                                item.pop("reason", None)
                                item.pop("orcaRemovalError", None)
                                continue
                            if item.get("orcaRemovalIntent"):
                                raise Conflict(
                                    "Previous Orca removal remains unconfirmed; do not dispatch it again"
                                )
                        target = item.get("target")
                        if not exists and not registered:
                            if target and os.path.lexists(target["checkout"]["gitdir"]):
                                raise Conflict("Checkout metadata remains after removal")
                            item["status"] = "removed" if item.get("removalIntent") else "absent"
                            item.pop("reason", None)
                            continue
                        if not exists or not registered:
                            raise Conflict("Checkout path and Git inventory disagree")
                        if target is None and previous_status in ("removed", "absent"):
                            raise Conflict("Previously absent checkout reappeared without identity")
                        current = local_target(path)
                        if Path(current["checkout"]["common"]).resolve() != repo_common:
                            raise Conflict("Worktree repository identity changed")
                        if target is not None and target != current:
                            raise Conflict("Cleanup target identity, branch or HEAD changed")
                        expected_state = workspace_state.get(str(path.resolve()))
                        if expected_state and (
                            current["head"] != expected_state[1]["head"]
                            or current["branch"] != "refs/heads/" + expected_state[1]["branch"]
                        ):
                            raise Conflict("Candidate branch or HEAD changed after delivery")
                        obsolete = obsolete_integration.removable(
                            store, track, path, candidate_head=repository_states[name]["head"]
                        )
                        if item.get("obsolete") and item["obsolete"] != obsolete:
                            raise Conflict("Original integration evidence changed during cleanup")
                        if obsolete:
                            item["obsolete"] = obsolete
                        elif command(
                            ["git", "status", "--porcelain", "--untracked-files=no"], path
                        ):
                            raise Conflict("Tracked user changes remain")
                        retirement = None
                        if item.get("retirement") or not clean_integration.is_ancestor(
                            str(repo_root), current["head"], repo_remote_head
                        ):
                            if obsolete:
                                raise Conflict("Conflicted integration HEAD is not included")
                            retirement = clean_integration.plan(
                                store,
                                track,
                                path,
                                current,
                                repo_remote_head,
                                connection,
                                previous=item.get("retirement"),
                                candidate_head=candidate_head,
                                repo_name=repo_name,
                            )
                        # Inspect all residue before deleting any file. Even bytecode needs proof.
                        artifacts = reclaim(store.path, track_id, path, dry_run=True)
                        item.update(target=current, head=current["head"], artifacts=artifacts)
                        if retirement:
                            item["retirement"] = retirement
                        if dry_run:
                            item["status"] = "would-remove"
                        else:
                            if retirement:
                                clean_integration.retain(retirement)
                            item.update(status="removing", removalIntent=True)
                            item.pop("reason", None)
                            write_json(destination, report)
                            reclaim(store.path, track_id, path)
                            if local_target(path) != current:
                                raise Conflict("Cleanup target changed before Git removal")
                            reclaim(store.path, track_id, path, dry_run=True)
                            if obsolete and (
                                obsolete_integration.removable(
                                    store,
                                    track,
                                    path,
                                    candidate_head=repository_states[name]["head"],
                                )
                                != obsolete
                            ):
                                raise Conflict(
                                    "Original integration evidence changed before removal"
                                )
                            if retirement:
                                clean_integration.plan(
                                    store,
                                    track,
                                    path,
                                    current,
                                    repo_remote_head,
                                    connection,
                                    previous=retirement,
                                    candidate_head=candidate_head,
                                    repo_name=repo_name,
                                )
                                clean_integration.check(
                                    store,
                                    track,
                                    path,
                                    current,
                                    retirement,
                                    repo_remote_head,
                                    connection,
                                    candidate_head=candidate_head,
                                    repo_name=repo_name,
                                )
                            if orca is None:
                                argv = ["git", "worktree", "remove"]
                                if obsolete:
                                    # Only the unchanged, archived and superseded merge may be dirty.
                                    argv.append("--force")
                                command([*argv, value], repo_root)
                            else:
                                orca.remove(lambda: write_json(destination, report))
                            if (
                                os.path.lexists(path)
                                or str(path.resolve()) in registered_worktrees(repo_root)
                                or os.path.lexists(current["checkout"]["gitdir"])
                            ):
                                raise Conflict("Git worktree removal is not confirmed")
                            item["status"] = "removed"
                        item.pop("reason", None)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RuntimeError,
                    subprocess.SubprocessError,
                ) as error:
                    item["status"] = "preserved"
                    item["reason"] = str(error)
                finally:
                    if not dry_run:
                        write_json(destination, report)
        report["branchesPreserved"] = all(
            item.get("branchPreserved", True) for item in report["worktrees"]
        )
        report["status"] = (
            "deferred"
            if delivery_error
            or any(r["status"] == "preserved" for r in report["worktrees"] + report["terminals"])
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
