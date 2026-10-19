"""Durable proposal adoption under the engine's checkout lock and live claim.

Recovery first proves the original request/revision from durable claim events.
Exact before states are released; exact after states are committed/adopted once.
Ambiguous states remain held: restore target bytes/modes and index from intent,
preserving later user work separately, then retry. Do not delete journal evidence.
"""

import base64
from contextlib import nullcontext
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


def claim_scope(connection, track, identity):
    """Recover claim-time authority from durable host events, not mutable task input."""
    origin = connection.execute(
        "SELECT a.task,a.generation,a.status,w.track,w.status AS task_status "
        "FROM attempts a JOIN tasks w ON w.id=a.task WHERE a.id=?",
        (identity.get("attempt"),),
    ).fetchone()
    if (
        not origin
        or origin["task"] != identity.get("task")
        or origin["generation"] != identity.get("generation")
        or origin["track"] != track
    ):
        raise Conflict("Proposal origin task/attempt is missing or mismatched; preserve evidence")
    request = revision = None
    matches = []
    for row in connection.execute(
        "SELECT type,body FROM events WHERE track=? AND type IN "
        "('document.registered','execution.accepted','worker.claimed') ORDER BY seq",
        (track,),
    ):
        body = json.loads(row["body"])
        if row["type"] == "document.registered":
            revision = body.get("revision")
        elif row["type"] == "execution.accepted":
            request = body.get("requestId")
        elif body.get("attemptId") == identity["attempt"]:
            if body.get("workId") != identity["task"]:
                raise Conflict("Proposal claim event disagrees with its attempt; preserve evidence")
            matches.append({"request": request, "revision": revision})
    if (
        len(matches) != 1
        or not matches[0]["request"]
        or type(matches[0]["revision"]) is not int
        or matches[0]["revision"] < 1
    ):
        raise Conflict("Proposal request/revision origin is ambiguous; inspect preserved events")
    return matches[0], origin


def require_scope(engine, task, record=None, connection=None):
    with nullcontext(connection) if connection is not None else engine.store.connect() as c:
        engine.store.assert_claim(c, task)
        identity = {**task, "task": task["id"]}
        current, _ = claim_scope(c, task["track"], identity)
        track = engine.store.track(task["track"], c)
        expected = {"request": track["request"], "revision": track["revision"]}
        if current != expected or current["revision"] != task["input_revision"]:
            raise Conflict("Current proposal request/revision changed; preserve evidence")
        if record is not None:
            intent = record["intent"]
            original, origin = claim_scope(c, task["track"], intent)
            if original != current or intent.get("scope", original) != original:
                raise Conflict(
                    f"Proposal request/revision mismatch: original={original}, current={current}; "
                    "preserve journal and checkout for inspection; do not delete evidence"
                )
            if intent["attempt"] != task["attempt"] and (
                origin["status"] == "running"
                or (intent["task"] != task["id"] and origin["task_status"] == "running")
            ):
                raise Conflict("Original proposal attempt is still running; reconcile it first")
        return current


def checkout_parts(workspace, pathspecs):
    return [
        command(["git", "ls-files", "--stage", "-z", "--", *pathspecs], workspace),
        command(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "--binary", "--", *pathspecs],
            workspace,
        ),
        command(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspecs],
            workspace,
        ),
    ]


def outside_state(workspace, files):
    return fingerprint(
        checkout_parts(workspace, [".", *[":(exclude,literal)" + row["path"] for row in files]])
    )


def index_entries(output):
    entries = {}
    for entry in filter(None, output.split("\0")):
        metadata, path = entry.split("\t", 1)
        entries.setdefault(path, []).append(metadata.split())
    return entries


def recovery_baseline(workspace, record):
    intent = record["intent"]
    if "outside" in intent and "index_entries" in intent:
        return intent["outside"], intent["index_entries"]
    # Legacy intents have a full snapshot but no separable outside checkpoint.
    # A clean original tree can be reconstructed without touching the checkout.
    # Anything more ambiguous remains held for manual inspection/restoration.
    entries = []
    for entry in filter(
        None, command(["git", "ls-tree", "-r", "-z", intent["before_head"]], workspace).split("\0")
    ):
        metadata, path = entry.split("\t", 1)
        mode, _, blob = metadata.split()
        entries.append(f"{mode} {blob} 0\t{path}\0")
    staged = "".join(entries)
    if (
        intent["merge_head"]
        or fingerprint([intent["before_head"], staged, "", ""]) != intent["snapshot"]
    ):
        raise Conflict("Legacy proposal baseline is ambiguous; restore from intent and retry")
    targets = {row["path"] for row in intent["files"]}
    outside = "".join(entry for entry in entries if entry.split("\t", 1)[1][:-1] not in targets)
    return fingerprint([outside, "", ""]), index_entries(staged)


def require_after(workspace, record):
    intent = record["intent"]
    if (
        command(["git", "rev-parse", "HEAD"], workspace) != intent["before_head"]
        or integration.merge_head(workspace) != intent["merge_head"]
        or any(file_state(workspace / row["path"]) != row["after"] for row in intent["files"])
    ):
        raise Conflict("Interrupted proposal preserved: HEAD, merge or target bytes/modes changed")
    outside, original_index = recovery_baseline(workspace, record)
    if outside_state(workspace, intent["files"]) != outside:
        raise Conflict("Interrupted proposal preserved: unrelated checkout/index changed")
    paths = {row["path"] for row in intent["files"]}
    current = index_entries(command(["git", "ls-files", "--stage", "-z"], workspace))
    current = {path: entries for path, entries in current.items() if path in paths}
    before = {path: entries for path, entries in original_index.items() if path in paths}
    after = {}
    for row in intent["files"]:
        mode = "100755" if row["after"]["mode"] & 0o100 else "100644"
        blob = command(
            ["git", "hash-object", "--path=" + row["path"], "--stdin"],
            workspace,
            input=base64.b64decode(row["after"]["bytes"]).decode("utf-8"),
        )
        after[row["path"]] = [[mode, blob, "0"]]
    if current not in (before, after):
        raise Conflict("Interrupted proposal preserved: target index is neither before nor after")


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
    if intent["merge_head"]:
        if command(["git", "status", "--porcelain=v1", "--untracked-files=all"], workspace):
            raise Conflict("Proposal merge checkout/index changed")
    else:
        outside, _ = recovery_baseline(workspace, record)
        if outside_state(workspace, intent["files"]) != outside:
            raise Conflict("Proposal unrelated checkout/index changed")
    return head


def save(engine, task, record):
    with engine.store.transaction() as connection:
        require_scope(engine, task, record, connection)
        write_json(journal_path(engine, task["track"]), record)


def adopt(engine, task, workspace, record):
    require_scope(engine, task, record)
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
    record = {
        **record,
        "phase": "committed",
        "head": head,
        "adopted_by": {"task": task["id"], "attempt": task["attempt"]},
    }
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
            and (
                (record.get("recovered_by") or {}).get("task") == task["id"]
                or (
                    record["intent"]["task"] == task["id"]
                    and record["intent"]["result"] is not None
                )
            )
            and record["intent"]["workspace"] == str(Path(workspace).resolve())
        ):
            require_scope(engine, task, record)
            prove_commit(Path(workspace), record)
            return record
        return None
    require_scope(engine, task, record)
    workspace = Path(workspace).resolve()
    intent = record["intent"]
    if str(workspace) != intent["workspace"]:
        raise Conflict("Unfinished proposal belongs to another checkout")
    head = command(["git", "rev-parse", "HEAD"], workspace)
    # Normal apply also adopts a commit. Only actual recovery may skip a worker
    # without a stored result; persist this receipt with the recovered adoption.
    recovery_record = {
        **record,
        "recovered_by": {"task": task["id"], "attempt": task["attempt"]},
    }
    if record["phase"] == "ready" and (head != intent["before_head"] or not record["commit"]):
        return adopt(engine, task, workspace, recovery_record)
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
    if (
        head == intent["before_head"]
        and integration.merge_head(workspace) == intent["merge_head"]
        and all(file_state(workspace / row["path"]) == row["after"] for row in intent["files"])
    ):
        require_after(workspace, record)
        if intent["repair"]:
            pending = integration.pending(engine.store.track(task["track"]))
            if not pending or any(
                pending[key] != intent["repair"][key] for key in ("candidate", "base")
            ):
                raise Conflict("Integration repair intent changed during recovery")
        return finish(engine, task, workspace, recovery_record)
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
        # A live merge is a new adoption; committed retries have cleared MERGE_HEAD.
        and not integration.merge_head(workspace)
    ):
        require_scope(engine, task, prior)
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
        "scope": require_scope(engine, task),
        "workspace": str(workspace),
        "before_head": expected_head,
        "merge_head": merging,
        "proposal": changes,
        "files": files,
        "snapshot": snapshot(workspace),
        "outside": outside_state(workspace, files),
        "index_entries": index_entries(command(["git", "ls-files", "--stage", "-z"], workspace)),
        "index": base64.b64encode(index.read_bytes()).decode("ascii"),
        "repair": repair,
        "result": result,
    }
    record = {"version": 1, "id": fingerprint(intent), "intent": intent, "phase": "applying"}
    # A single durable intent precedes every target write, including mkdir.
    with engine.store.transaction() as connection:
        require_scope(engine, task, record, connection)
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
    return finish(engine, task, workspace, record)


def finish(engine, task, workspace, record):
    require_scope(engine, task, record)
    require_after(workspace, record)
    intent = record["intent"]
    files = intent["files"]
    paths = [row["path"] for row in files]
    pathspecs = specs(record)
    repair, merging = intent["repair"], intent["merge_head"]
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
        require_scope(engine, task, record)
        require_after(workspace, record)
        command(
            ["git", "commit", "-m", record["message"]]
            + ([] if repair else ["--only", "--", *pathspecs]),
            workspace,
        )
    return adopt(engine, task, workspace, record)
