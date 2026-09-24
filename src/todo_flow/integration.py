"""Durable landing repair: merge the pinned base into the owned candidate checkout."""

import json
import subprocess

from .adapters import command
from .store import Conflict, encode
from .checkout import snapshot, require_snapshot


def merge_head(workspace):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "MERGE_HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def unmerged(workspace):
    output = command(["git", "ls-files", "--unmerged", "-z"], workspace)
    rows = {}
    for entry in output.split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        mode, blob, stage = metadata.split()
        rows.setdefault(path, {})[stage] = {"mode": mode, "blob": blob}
    return rows


def pending(track):
    record = json.loads(track["landing"]) if track["landing"] else {}
    return record if record.get("status") == "integration-repair" else None


def request_repair(engine, task, integration, base, reason):
    record = {
        "status": "integration-repair",
        "phase": "pending",
        "candidate": engine.store.track(task["track"])["head"],
        "base": base,
        "integration": str(integration),
        "reason": reason,
    }
    engine.update(task, landing=encode(record), review=None, verification=None)
    return followup(record)


def followup(record):
    return {
        "summary": record["reason"],
        "next": [
            {
                "kind": "work",
                "purpose": "Repair integration against the current base. Read integration_repair "
                "evidence and resolve the prepared candidate checkout; verification and a new "
                "independent review are required before landing.",
            }
        ],
    }


def prepare(engine, task, workspace):
    record = pending(engine.store.track(task["track"]))
    if not record:
        return None
    current = command(["git", "rev-parse", "HEAD"], workspace)
    merging = merge_head(workspace)
    recovered = False
    if current != record["candidate"]:
        # A previous attempt may have committed the merge before its state update was persisted.
        parents = command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split()
        if (
            parents != [record["candidate"], record["base"]]
            or merging
            or command(["git", "status", "--porcelain"], workspace)
            or command(["git", "rev-parse", "HEAD^{tree}"], workspace)
            != record.get("resolved_tree")
        ):
            raise Conflict("Integration repair checkout changed; inspect before resuming")
        recovered = True
    if not recovered and record.get("checkout"):
        require_snapshot(workspace, record["checkout"])
    if merging and not record.get("checkout"):
        raise Conflict(
            "Merge preparation was interrupted before its checkpoint; inspect preserved state"
        )
    if record["phase"] == "pending":
        if merging or command(["git", "status", "--porcelain"], workspace):
            raise Conflict("Integration repair requires a clean owned checkout; changes preserved")
        command(["git", "fetch", "origin", engine.config["base"]], engine.root)
        record["base"] = command(
            ["git", "rev-parse", "origin/" + engine.config["base"]], engine.root
        )
        record["phase"] = "merging"
        # Persist the exact merge parents before touching the index. Retries use this same base.
        engine.update(task, landing=encode(record), review=None, verification=None)
    if merging and merging != record["base"]:
        raise Conflict("Another merge is in progress; preserve it for inspection")
    if not merging and not recovered:
        if command(["git", "status", "--porcelain"], workspace):
            raise Conflict("Integration repair checkout has unrelated changes; preserved")
        with engine.store.transaction() as connection:
            engine.store.assert_claim(connection, task)
        try:
            command(
                [
                    "git",
                    "-c",
                    "merge.conflictStyle=diff3",
                    "-c",
                    "rerere.enabled=false",
                    "merge",
                    "--no-ff",
                    "--no-commit",
                    record["base"],
                ],
                workspace,
            )
        except RuntimeError:
            if not unmerged(workspace) or merge_head(workspace) != record["base"]:
                raise
    if not recovered and not record.get("checkout"):
        record["checkout"] = snapshot(workspace)
        engine.update(task, landing=encode(record))
    folder = engine.store.path / "attempts" / task["attempt"] / "conflicts"
    folder.mkdir(parents=True, exist_ok=True)
    base_diff = folder / "base.patch"
    base_diff.write_text(
        command(["git", "diff", record["candidate"] + "..." + record["base"]], workspace)
    )
    conflicts = []
    for index, (path, stages) in enumerate(unmerged(workspace).items()):
        item = {"path": path, "versions": {}}
        for stage, entry in stages.items():
            role = {"1": "ancestor", "2": "candidate", "3": "base"}[stage]
            target = folder / str(index) / role
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.check_output(["git", "cat-file", "blob", entry["blob"]], cwd=workspace)
            )
            item["versions"][role] = {**entry, "path": str(target)}
        conflicts.append(item)
    return {
        **record,
        "workspace_head": current,
        "merge_head": merge_head(workspace),
        "base_diff": str(base_diff),
        "conflicts": conflicts,
    }


def validate_resolution(workspace, changes, record):
    if command(["git", "rev-parse", "HEAD"], workspace) != record["workspace_head"]:
        raise Conflict("Integration repair HEAD changed during worker execution")
    if merge_head(workspace) != record["merge_head"]:
        raise Conflict("Integration repair merge changed during worker execution")
    if record["workspace_head"] == record["candidate"]:
        require_snapshot(workspace, record["checkout"])
    elif command(["git", "status", "--porcelain", "--untracked-files=all"], workspace):
        raise Conflict("Recovered repair checkout changed; preserve it for inspection")
    conflicts = unmerged(workspace)
    proposed = {change["path"]: change["content"] for change in changes}
    if not conflicts.keys() <= proposed.keys():
        raise Conflict("Proposal must resolve every unmerged path, or ask a concrete question")
    for path in conflicts:
        if any(
            line.startswith(("<<<<<<< ", "||||||| ", ">>>>>>> ")) or line == "======="
            for line in proposed[path].splitlines()
        ):
            raise Conflict("Proposal retains conflict markers: " + path)


def record_resolution(engine, task, workspace, record):
    latest = pending(engine.store.track(task["track"]))
    if not latest or latest["candidate"] != record["candidate"] or latest["base"] != record["base"]:
        raise Conflict("Integration repair intent changed")
    latest["resolved_tree"] = command(["git", "write-tree"], workspace)
    latest["checkout"] = snapshot(workspace)
    engine.update(task, landing=encode(latest))


def finish_repair(engine, task, workspace, record):
    if merge_head(workspace) or unmerged(workspace):
        raise Conflict("Integration repair has unresolved merge state")
    head = command(["git", "rev-parse", "HEAD"], workspace)
    for parent in (record["candidate"], record["base"]):
        command(["git", "merge-base", "--is-ancestor", parent, head], workspace)
    engine.update(task, head=head, landing=None, verification=None, review=None)
