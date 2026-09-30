"""Durable proposal adoption under the engine's checkout lock and live claim.

An interrupted write is never guessed complete. Restore the target bytes/modes
and index from intent.files[*].before and intent.index (base64), preserving any
later user work separately, then retry the work task. Recovery accepts that
boundary only when the original HEAD, merge, files and checkout snapshot match.
Do not delete the journal to bypass a hold.
"""

import base64
import json
import os
import stat
import tempfile
from pathlib import Path

from . import integration
from .adapters import command
from .change_proposal import prepare_changes, validate_changes
from .checkout import snapshot
from .maintenance import write_json
from .store import Conflict, encode, fingerprint


ACTIVE = {"applying", "ready"}


def journal_path(engine, track):
    return engine.store.path / ("proposal-" + track + ".json")


def read(engine, track):
    path = journal_path(engine, track)
    if not os.path.lexists(path):
        return None
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        record = json.loads(path.read_text(encoding="utf-8"))
        if type(record["version"]) is not int or record["version"] != 1:
            raise ValueError("unsupported version")
        intent = record["intent"]
        if (
            record["phase"] not in ACTIVE | {"committed", "restored"}
            or intent["track"] != track
            or record["id"] != fingerprint(intent)
            or not isinstance(intent["files"], list)
        ):
            raise ValueError("invalid binding")
        validate_changes(intent["proposal"])
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Conflict(f"Invalid or unsupported proposal journal: {path}; preserve it") from error


def require_clear(engine, track):
    record = read(engine, track)
    if record and record["phase"] in ACTIVE:
        raise Conflict(f"Unfinished proposal application: {journal_path(engine, track)}")


def file_state(path):
    if path.is_symlink():
        raise Conflict("Proposal target became a symlink")
    if not path.exists():
        return None
    if not path.is_file():
        raise Conflict("Proposal target is no longer a regular file")
    return {
        "bytes": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


def atomic_file(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".todo-proposal-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def specs(record):
    return [":(literal)" + row["path"] for row in record["intent"]["files"]]


def changed_paths(workspace, before, after):
    output = command(["git", "diff", "--name-only", "-z", before, after], workspace)
    return sorted(filter(None, output.split("\0")))


def prove_commit(workspace, record):
    """No write here: require the exact recorded commit and every target result."""
    intent = record["intent"]
    head = command(["git", "rev-parse", "HEAD"], workspace)
    if integration.merge_head(workspace) or integration.unmerged(workspace):
        raise Conflict("Proposal still has unresolved merge state")
    if record["commit"]:
        parents = command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split()
        expected = [intent["before_head"]]
        if intent["merge_head"]:
            expected.append(intent["merge_head"])
        message = command(["git", "show", "-s", "--format=%B", "HEAD"], workspace)
        if parents != expected or message != record["message"]:
            raise Conflict("Proposal commit parent or identity marker does not match")
    elif head != intent["before_head"]:
        raise Conflict("No-op proposal HEAD changed")
    if record.get("head") and record["head"] != head:
        raise Conflict("Proposal receipt HEAD changed")
    if changed_paths(workspace, intent["before_head"], head) != record["changed_paths"]:
        raise Conflict("Proposal commit changed unexpected paths")
    if intent["merge_head"]:
        if command(["git", "rev-parse", "HEAD^{tree}"], workspace) != record["resolved_tree"]:
            raise Conflict("Proposal merge tree changed")
    for row in intent["files"]:
        if file_state(workspace / row["path"]) != row["after"]:
            raise Conflict("Proposal target result changed: " + row["path"])
        entry = command(["git", "ls-tree", "HEAD", "--", row["path"]], workspace)
        metadata = entry.split("\t", 1)[0].split()
        if len(metadata) != 3 or [metadata[0], metadata[2]] != record["entries"][row["path"]]:
            raise Conflict("Committed proposal target differs: " + row["path"])
    if specs(record) and command(
        ["git", "diff", "--cached", "--name-only", "--", *specs(record)], workspace
    ):
        raise Conflict("Proposal target index changed")
    return head


def save(engine, task, record):
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)
        write_json(journal_path(engine, task["track"]), record)


def adopt(engine, task, workspace, record):
    head = prove_commit(workspace, record)
    # Keep this hold until the track invalidation is durable. A crash on either
    # side can repeat this update, but cannot repeat the commit.
    if record["intent"]["merge_head"]:
        pending = integration.pending(engine.store.track(task["track"]))
        if (
            not pending
            or pending["candidate"] != record["intent"]["before_head"]
            or pending["base"] != record["intent"]["merge_head"]
            or pending.get("resolved_tree") != record["resolved_tree"]
        ):
            raise Conflict("Integration repair resolution receipt changed")
    if not record["intent"]["repair"] and (
        head != record["intent"]["before_head"] or engine.store.track(task["track"])["head"] != head
    ):
        engine.update(task, head=head, review=None, verification=None, landing=None)
    record = {**record, "phase": "committed", "head": head}
    save(engine, task, record)
    return record


def recover(engine, task, workspace):
    engine.process_barrier(task["track"]).require_clear()
    engine.check_claim(task)
    record = read(engine, task["track"])
    if not record:
        return None
    if record["phase"] not in ACTIVE:
        if (
            record["phase"] == "committed"
            and record["intent"]["task"] == task["id"]
            and record["intent"]["result"] is not None
            and record["intent"]["workspace"] == str(Path(workspace).resolve())
        ):
            prove_commit(Path(workspace), record)
            return record
        return None
    workspace = Path(workspace).resolve()
    intent = record["intent"]
    if str(workspace) != intent["workspace"]:
        raise Conflict("Unfinished proposal belongs to another checkout")
    head = command(["git", "rev-parse", "HEAD"], workspace)
    if record["phase"] == "ready" and (head != intent["before_head"] or not record["commit"]):
        return adopt(engine, task, workspace, record)
    if (
        head == intent["before_head"]
        and integration.merge_head(workspace) == intent["merge_head"]
        and snapshot(workspace) == intent["snapshot"]
        and all(file_state(workspace / row["path"]) == row["before"] for row in intent["files"])
    ):
        if intent["repair"]:
            pending = integration.pending(engine.store.track(task["track"]))
            if not pending or any(
                pending[key] != intent["repair"][key] for key in ("candidate", "base")
            ):
                raise Conflict("Integration repair intent changed during restoration")
            engine.update(task, landing=encode(intent["repair"]), review=None, verification=None)
        save(engine, task, {**record, "phase": "restored"})
        return None
    raise Conflict(
        f"Interrupted proposal preserved at {journal_path(engine, task['track'])}; "
        "restore original target bytes/modes and index from intent, then retry. "
        "Recovery requires the original HEAD, merge and checkout snapshot; do not delete evidence."
    )


def apply(engine, task, workspace, changes, repair=None, expected_head=None, result=None):
    engine.process_barrier(task["track"]).require_clear()
    engine.check_claim(task)
    workspace = Path(workspace).resolve()
    prior = read(engine, task["track"])
    validate_changes(changes)
    if prior and prior["phase"] in ACTIVE:
        recover(engine, task, workspace)
        prior = read(engine, task["track"])
    if (
        prior
        and prior["phase"] == "committed"
        and prior["intent"]["proposal"] == changes
        and prior["intent"]["workspace"] == str(workspace)
        and expected_head in (None, prior["intent"]["before_head"])
        and command(["git", "rev-parse", "HEAD"], workspace) == prior["head"]
    ):
        prove_commit(workspace, prior)
        return
    if expected_head is None:
        expected_head = (
            repair["workspace_head"] if repair else engine.store.track(task["track"])["head"]
        )
    if not expected_head or command(["git", "rev-parse", "HEAD"], workspace) != expected_head:
        raise Conflict("Proposal HEAD changed during worker execution")
    prepared = prepare_changes(
        workspace, changes, engine.config["writable_patterns"], expected_head
    )
    if repair:
        integration.validate_resolution(workspace, prepared, repair)
    paths = [row["path"] for row in prepared]
    pathspecs = [":(literal)" + name for name in paths]
    merging = integration.merge_head(workspace)
    if not repair:
        if merging or integration.unmerged(workspace):
            raise Conflict("An unowned merge is in progress; preserve it for inspection")
        if paths and command(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *pathspecs],
            workspace,
        ):
            raise Conflict("Proposal overlaps existing changes; preserve them before applying it")
    files = []
    for row in prepared:
        before = file_state(workspace / row["path"])
        files.append(
            {
                "path": row["path"],
                "before": before,
                "after": {
                    "bytes": base64.b64encode(row["content"].encode("utf-8")).decode("ascii"),
                    "mode": before["mode"] if before else 0o644,
                },
            }
        )
    index = Path(command(["git", "rev-parse", "--git-path", "index"], workspace))
    if not index.is_absolute():
        index = workspace / index
    intent = {
        "track": task["track"],
        "task": task["id"],
        "attempt": task["attempt"],
        "generation": task["generation"],
        "workspace": str(workspace),
        "before_head": expected_head,
        "merge_head": merging,
        "proposal": changes,
        "files": files,
        "snapshot": snapshot(workspace),
        "index": base64.b64encode(index.read_bytes()).decode("ascii"),
        "repair": repair,
        "result": result,
    }
    record = {"version": 1, "id": fingerprint(intent), "intent": intent, "phase": "applying"}
    # A single durable intent precedes every target write, including mkdir.
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)
        if command(["git", "rev-parse", "HEAD"], workspace) != expected_head:
            raise Conflict("Proposal HEAD changed before application")
        if (
            snapshot(workspace) != intent["snapshot"]
            or any(file_state(workspace / row["path"]) != row["before"] for row in files)
            or prepare_changes(
                workspace, changes, engine.config["writable_patterns"], expected_head
            )
            != prepared
        ):
            raise Conflict("Proposal source changed before application")
        if repair:
            integration.validate_resolution(workspace, prepared, repair)
        elif paths and command(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *pathspecs],
            workspace,
        ):
            raise Conflict("Proposal overlaps existing changes; preserve them before applying it")
        write_json(journal_path(engine, task["track"]), record)
        for row in files:
            atomic_file(
                workspace / row["path"],
                base64.b64decode(row["after"]["bytes"]),
                row["after"]["mode"],
            )
    engine.check_claim(task)
    if paths:
        command(["git", "add", "--", *pathspecs], workspace)
    if integration.unmerged(workspace):
        raise Conflict("Cannot commit unresolved merge paths")
    dirty = (
        command(
            ["git", "diff", "--cached", "--name-only", "-z"]
            + ([] if repair else ["--", *pathspecs]),
            workspace,
        )
        if (paths or repair)
        else ""
    )
    entries = {}
    for row in files:
        staged = command(
            ["git", "ls-files", "--stage", "--", ":(literal)" + row["path"]], workspace
        )
        mode, blob, stage = staged.split("\t", 1)[0].split()
        if stage != "0":
            raise Conflict("Unresolved proposal index")
        entries[row["path"]] = [mode, blob]
    record.update(
        phase="ready",
        commit=bool(dirty or (repair and merging)),
        changed_paths=sorted(filter(None, dirty.split("\0"))),
        entries=entries,
        message="Implement " + task["track"] + "\n\nTODO-Flow-Proposal: " + record["id"],
    )
    if repair and merging:
        integration.record_resolution(engine, task, workspace, repair)
        record["resolved_tree"] = command(["git", "write-tree"], workspace)
    save(engine, task, record)
    if record["commit"]:
        engine.check_claim(task)
        command(
            ["git", "commit", "-m", record["message"]]
            + ([] if repair else ["--only", "--", *pathspecs]),
            workspace,
        )
    adopt(engine, task, workspace, record)
