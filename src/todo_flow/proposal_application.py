"""Durable proposal application and state-based recovery under a live claim.

Recovery closes an interrupted journal only when checkout matches its exact
before or proposal-after state. Any mixed state remains preserved for inspection.
"""

import base64
import json
import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

from . import integration, repositories
from .adapters import command
from .change_proposal import prepare_changes, validate_changes
from .checkout import snapshot
from .maintenance import write_json
from .store import Conflict, encode, fingerprint


ACTIVE = {"applying", "ready"}


def journal_path(engine, track, repo_name=None):
    suffix = "" if repo_name is None else "-" + repo_name
    return engine.store.path / ("proposal-" + track + suffix + ".json")


def batch_path(engine, track):
    return engine.store.path / ("proposal-batch-" + track + ".json")


def read_batch(engine, track):
    path = batch_path(engine, track)
    if not os.path.lexists(path):
        return None
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        record = json.loads(path.read_text(encoding="utf-8"))
        intent = record["intent"]
        if (
            record["version"] != 1
            or record["phase"] not in ACTIVE | {"committed"}
            or intent["track"] != track
            or record["id"] != fingerprint(intent)
            or not isinstance(intent["repositories"], list)
            or not isinstance(intent["base_heads"], dict)
            or not isinstance(intent["changes"], list)
        ):
            raise ValueError("invalid binding")
        validate_changes(intent["changes"])
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Conflict(
            f"Invalid or unsupported multi-repository proposal journal: {path}; preserve it"
        ) from error


def begin_batch(engine, task, base_heads, grouped, changes, result):
    intent = {
        "track": task["track"],
        "task": task["id"],
        "attempt": task["attempt"],
        "generation": task["generation"],
        "base_heads": dict(base_heads),
        "aggregate_head": repositories.fingerprint(base_heads),
        "repositories": sorted(grouped),
        "changes": changes,
        "result": result,
    }
    record = {"version": 1, "id": fingerprint(intent), "intent": intent, "phase": "applying"}
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)
        previous = read_batch(engine, task["track"])
        if previous and previous["phase"] in ACTIVE:
            raise Conflict("An unfinished multi-repository proposal batch already exists")
        write_json(batch_path(engine, task["track"]), record)
    return record


def finish_batch(engine, task, record):
    heads = repositories.head_map(engine.store.track(task["track"]), engine.config)
    completed = {**record, "phase": "committed", "result_heads": heads}
    completed["id"] = fingerprint(completed["intent"])
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)
        if read_batch(engine, task["track"]) != record:
            raise Conflict("Multi-repository proposal batch changed before completion")
        write_json(batch_path(engine, task["track"]), completed)
    return completed


def read(engine, track, repo_name=None):
    if repositories.multi(engine.config) and repo_name is None:
        return {
            spec["name"]: read(engine, track, spec["name"])
            for spec in repositories.specs(engine.config)
        }
    path = journal_path(engine, track, repo_name)
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
            or intent.get("repository") != repo_name
            or record["id"] != fingerprint(intent)
            or not isinstance(intent["files"], list)
        ):
            raise ValueError("invalid binding")
        validate_changes(intent["proposal"])
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Conflict(f"Invalid or unsupported proposal journal: {path}; preserve it") from error


def _registered_workspace(engine, track, repo_name):
    current = engine.store.track(track)
    if repositories.multi(engine.config):
        return engine.repository_state(current, repo_name)["workspace"]
    return current.get("workspace")


def _workspace_outside_snapshot(workspace, paths):
    pathspec = [".", *[":(exclude,literal)" + path for path in paths]]
    return fingerprint(
        [
            command(
                ["git", "diff", "--no-ext-diff", "--no-textconv", "--binary", "--", *pathspec],
                workspace,
            ),
            command(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspec],
                workspace,
            ),
        ]
    )


def _index_entries(workspace, index=None):
    env = dict(os.environ)
    env.pop("GIT_INDEX_FILE", None)
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Conflict("Cannot inspect proposal recovery index; preserve the journal") from error
    if result.returncode:
        raise Conflict("Cannot inspect proposal recovery index; preserve the journal")
    return result.stdout


def _recovery_index_state(engine, workspace, record, *, compare_after=True):
    intent = record["intent"]
    with tempfile.TemporaryDirectory(prefix="todo-flow-proposal-index-") as folder:
        index = Path(folder) / "index"
        index.write_bytes(base64.b64decode(intent["index"], validate=True))
        before = _index_entries(workspace, index)
        current = _index_entries(workspace)
        if current == before:
            return "before"
        if not compare_after:
            return None
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        try:
            staged = subprocess.run(
                ["git", "add", "--", *specs(record)],
                cwd=workspace,
                env=env,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Conflict("Cannot verify proposal recovery index; preserve the journal") from error
        if staged.returncode:
            raise Conflict("Cannot verify proposal recovery index; preserve the journal")
        after = _index_entries(workspace, index)
    if current == after:
        return "after"
    return None


def _generation_matches(workspace, intent):
    return (
        command(["git", "rev-parse", "HEAD"], workspace) == intent["before_head"]
        and integration.merge_head(workspace) == intent["merge_head"]
    )


def _outside_snapshot_matches(workspace, intent):
    return not intent.get("outside_snapshot") or (
        _workspace_outside_snapshot(workspace, [row["path"] for row in intent["files"]])
        == intent["outside_snapshot"]
    )


def _after_state(engine, task, workspace, record, repo_name):
    intent = record["intent"]
    if not _generation_matches(workspace, intent) or any(
        file_state(workspace / row["path"]) != row["after"] for row in intent["files"]
    ):
        return None
    if not _outside_snapshot_matches(workspace, intent):
        return None
    repair = intent.get("repair")
    if repair:
        integration.validate_resolution(
            workspace, _after_changes(record), repair, check_snapshot=False
        )
        pending = integration.pending(engine.store.track(task["track"]))
        if (
            not pending
            or pending["candidate"] != repair["candidate"]
            or pending["base"] != repair["base"]
            or pending.get("repository") != repo_name
        ):
            raise Conflict("Integration repair intent changed during proposal recovery")
    elif integration.unmerged(workspace):
        return None
    return _recovery_index_state(engine, workspace, record)


def _after_changes(record):
    return [
        {
            "path": row["path"],
            "content": base64.b64decode(row["after"]["bytes"], validate=True).decode(
                "utf-8", errors="strict"
            ),
        }
        for row in record["intent"]["files"]
    ]


def _partial_after_state(engine, task, workspace, record, repo_name):
    """Accept only journal-described file mixtures before the proposal index write."""
    intent = record["intent"]
    if record["phase"] != "applying" or not _generation_matches(workspace, intent):
        return False
    states = [file_state(workspace / row["path"]) for row in intent["files"]]
    if not any(state == row["after"] for state, row in zip(states, intent["files"])):
        return False
    if not all(
        state == row["before"] or state == row["after"]
        for state, row in zip(states, intent["files"])
    ):
        return False
    if not _outside_snapshot_matches(workspace, intent):
        return False
    repair = intent.get("repair")
    if repair:
        integration.validate_resolution(
            workspace, _after_changes(record), repair, check_snapshot=False
        )
        pending = integration.pending(engine.store.track(task["track"]))
        if (
            not pending
            or pending["candidate"] != repair["candidate"]
            or pending["base"] != repair["base"]
            or pending.get("repository") != repo_name
        ):
            raise Conflict("Integration repair intent changed during proposal recovery")
    elif integration.unmerged(workspace):
        return False
    return _recovery_index_state(engine, workspace, record, compare_after=False) == "before"


def require_clear(engine, track, task=None):
    records = read(engine, track)
    if repositories.multi(engine.config):
        records = {name: record for name, record in records.items() if record is not None}
    else:
        records = {None: records} if records else {}
    if task is not None:
        for name, record in records.items():
            if record["phase"] not in ACTIVE:
                continue
            workspace = _registered_workspace(engine, track, name)
            if not workspace or str(Path(workspace).resolve()) != record["intent"]["workspace"]:
                raise _manual_recovery_conflict(engine, track, workspace, name)
            recover(engine, task, workspace, name)
        records = read(engine, track)
        if repositories.multi(engine.config):
            records = {name: record for name, record in records.items() if record is not None}
        else:
            records = {None: records} if records else {}
        if repositories.multi(engine.config):
            batch = read_batch(engine, track)
            if batch and batch["phase"] in ACTIVE:
                workspaces = {
                    spec["name"]: _registered_workspace(engine, track, spec["name"])
                    for spec in repositories.specs(engine.config)
                }
                if any(not workspace for workspace in workspaces.values()):
                    raise Conflict(
                        f"Unfinished multi-repository proposal application: {batch_path(engine, track)}; "
                        "a registered workspace is missing, preserve the journal"
                    )
                recover(engine, task, workspaces)
                batch = read_batch(engine, track)
                if batch and batch["phase"] in ACTIVE:
                    raise Conflict(
                        f"Unfinished multi-repository proposal application: {batch_path(engine, track)}"
                    )
    for name, record in records.items():
        if record["phase"] in ACTIVE:
            raise Conflict(f"Unfinished proposal application: {journal_path(engine, track, name)}")


def _manual_recovery_conflict(engine, track, workspace, repo_name):
    journal = journal_path(engine, track, repo_name)
    workspace_arg = shlex.quote(str(workspace)) if workspace else "<registered-workspace>"
    state_arg = shlex.quote(str(engine.store.path))
    return Conflict(
        f"Unfinished proposal application: interrupted proposal preserved at {journal}; "
        "workspace matches neither the recorded before state nor the exact proposal after state. "
        "Inspect with "
        f"`git -C {workspace_arg} status --short`; restore target bytes/modes and the index "
        "from this journal, then resume with "
        f"`todo-flow --state {state_arg} run`. Do not delete the journal."
    )


def _entries(workspace, record):
    result = {}
    for row in record["intent"]["files"]:
        staged = command(
            ["git", "ls-files", "--stage", "--", ":(literal)" + row["path"]], workspace
        )
        fields = staged.split("\t", 1)[0].split()
        if len(fields) != 3 or fields[2] != "0":
            raise Conflict("Proposal index is unresolved; preserve the journal")
        result[row["path"]] = fields[:2]
    return result


def _ready_record(engine, task, workspace, record):
    intent = record["intent"]
    pathspecs = specs(record)
    repair = intent.get("repair")
    merging = intent["merge_head"]
    dirty = (
        command(
            ["git", "diff", "--cached", "--name-only", "-z"]
            + ([] if repair else ["--", *pathspecs]),
            workspace,
        )
        if (pathspecs or repair)
        else ""
    )
    ready = {
        **record,
        "phase": "ready",
        "commit": bool(dirty or (repair and merging)),
        "changed_paths": sorted(filter(None, dirty.split("\0"))),
        "entries": _entries(workspace, record),
        "message": "Implement " + task["track"] + "\n\nTODO-Flow-Proposal: " + record["id"],
    }
    if repair and merging:
        integration.record_resolution(engine, task, workspace, repair)
        ready["resolved_tree"] = command(["git", "write-tree"], workspace)
    save(engine, task, ready)
    return ready


def _validate_ready(workspace, record):
    intent = record["intent"]
    pathspecs = specs(record)
    repair = intent.get("repair")
    merging = intent["merge_head"]
    dirty = (
        command(
            ["git", "diff", "--cached", "--name-only", "-z"]
            + ([] if repair else ["--", *pathspecs]),
            workspace,
        )
        if (pathspecs or repair)
        else ""
    )
    expected = {
        "commit": bool(dirty or (repair and merging)),
        "changed_paths": sorted(filter(None, dirty.split("\0"))),
        "entries": _entries(workspace, record),
        "message": "Implement " + intent["track"] + "\n\nTODO-Flow-Proposal: " + record["id"],
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise Conflict("Ready proposal metadata changed; preserve the journal")
    if (
        repair
        and merging
        and record.get("resolved_tree") != command(["git", "write-tree"], workspace)
    ):
        raise Conflict("Ready proposal merge tree changed; preserve the journal")


def _commit_and_adopt(engine, task, workspace, record):
    pathspecs = specs(record)
    if record["commit"]:
        engine.check_claim(task)
        command(
            ["git", "commit", "-m", record["message"]]
            + ([] if record["intent"].get("repair") else ["--only", "--", *pathspecs]),
            workspace,
        )
    return adopt(engine, task, workspace, record)


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
        write_json(
            journal_path(engine, task["track"], record["intent"].get("repository")),
            record,
        )


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
            or pending.get("repository") != record["intent"].get("repository")
        ):
            raise Conflict("Integration repair resolution receipt changed")
    repo_name = record["intent"].get("repository")
    if not record["intent"]["repair"]:
        current = engine.repository_state(engine.store.track(task["track"]), repo_name)["head"]
        if head != record["intent"]["before_head"] or current != head:
            if repositories.multi(engine.config):
                engine.update_repository_state(task, repo_name, invalidate=True, head=head)
            else:
                engine.update(task, head=head, review=None, verification=None, landing=None)
    record = {**record, "phase": "committed", "head": head}
    save(engine, task, record)
    return record


def recover(engine, task, workspace, repo_name=None):
    if repositories.multi(engine.config) and repo_name is None:
        if not isinstance(workspace, dict):
            raise ValueError("Multi-repository recovery requires named workspaces")
        for spec in repositories.specs(engine.config):
            recover(engine, task, workspace[spec["name"]], spec["name"])
        batch = read_batch(engine, task["track"])
        if not batch:
            return None
        intent = batch["intent"]
        if batch["phase"] == "committed":
            if intent["task"] != task["id"]:
                return None
            if repositories.head_map(engine.store.track(task["track"]), engine.config) != batch.get(
                "result_heads"
            ):
                raise Conflict("Committed multi-repository proposal HEAD map changed")
            repositories.require_clean(engine.store.track(task["track"]), engine.config, workspace)
            return {"intent": {"task": task["id"], "result": intent["result"]}, "head": None}
        if set(intent["base_heads"]) != {row["name"] for row in repositories.specs(engine.config)}:
            raise Conflict("Multi-repository proposal no longer matches configured repositories")
        engine.apply_changes(
            task,
            workspace,
            intent["changes"],
            expected_head=intent["aggregate_head"],
            result=intent["result"],
            _batch=batch,
        )
        return {"intent": {"task": task["id"], "result": intent["result"]}, "head": None}
    engine.process_barrier(task["track"]).require_clear()
    engine.check_claim(task)
    record = read(engine, task["track"], repo_name)
    if not record:
        return None
    if record["phase"] not in ACTIVE:
        if (
            record["phase"] == "committed"
            and record["intent"]["task"] == task["id"]
            and record["intent"]["result"] is not None
            and record["intent"].get("repository") == repo_name
            and record["intent"]["workspace"] == str(Path(workspace).resolve())
        ):
            prove_commit(Path(workspace), record)
            return record
        return None
    workspace = Path(workspace).resolve()
    intent = record["intent"]
    registered = _registered_workspace(engine, task["track"], repo_name)
    if (
        str(workspace) != intent["workspace"]
        or not registered
        or str(Path(registered).resolve()) != str(workspace)
    ):
        raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
    head = command(["git", "rev-parse", "HEAD"], workspace)
    if record["phase"] == "ready" and head != intent["before_head"]:
        return adopt(engine, task, workspace, record)
    try:
        before_state = (
            record["phase"] == "applying"
            and head == intent["before_head"]
            and integration.merge_head(workspace) == intent["merge_head"]
            and snapshot(workspace) == intent["snapshot"]
            and all(file_state(workspace / row["path"]) == row["before"] for row in intent["files"])
        )
    except (OSError, ValueError, TypeError, KeyError, Conflict, RuntimeError) as error:
        raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name) from error
    if before_state:
        if intent["repair"]:
            pending = integration.pending(engine.store.track(task["track"]))
            if (
                not pending
                or any(pending[key] != intent["repair"][key] for key in ("candidate", "base"))
                or pending.get("repository") != repo_name
            ):
                raise Conflict("Integration repair intent changed during restoration")
            engine.update(task, landing=encode(intent["repair"]), review=None, verification=None)
        save(engine, task, {**record, "phase": "restored"})
        return None
    try:
        index_state = _after_state(engine, task, workspace, record, repo_name)
    except (OSError, ValueError, TypeError, KeyError, Conflict, RuntimeError) as error:
        raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name) from error
    if index_state == "after":
        if record["phase"] == "applying":
            if integration.unmerged(workspace):
                raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
            record = _ready_record(engine, task, workspace, record)
        else:
            _validate_ready(workspace, record)
        return _commit_and_adopt(engine, task, workspace, record)
    try:
        partial_after = _partial_after_state(engine, task, workspace, record, repo_name)
    except (OSError, ValueError, TypeError, KeyError, Conflict, RuntimeError) as error:
        raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name) from error
    if partial_after:
        engine.check_claim(task)
        if not _partial_after_state(engine, task, workspace, record, repo_name):
            raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
        for row in intent["files"]:
            if file_state(workspace / row["path"]) == row["before"]:
                atomic_file(
                    workspace / row["path"],
                    base64.b64decode(row["after"]["bytes"], validate=True),
                    row["after"]["mode"],
                )
        if (
            not _generation_matches(workspace, intent)
            or not all(
                file_state(workspace / row["path"]) == row["after"] for row in intent["files"]
            )
            or not _outside_snapshot_matches(workspace, intent)
            or _recovery_index_state(engine, workspace, record, compare_after=False) != "before"
        ):
            raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
        engine.check_claim(task)
        command(["git", "add", "--", *specs(record)], workspace)
        if (
            not _generation_matches(workspace, intent)
            or not _outside_snapshot_matches(workspace, intent)
            or _recovery_index_state(engine, workspace, record) != "after"
        ):
            raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
        if integration.unmerged(workspace):
            raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)
        record = _ready_record(engine, task, workspace, record)
        return _commit_and_adopt(engine, task, workspace, record)
    raise _manual_recovery_conflict(engine, task["track"], workspace, repo_name)


def apply(
    engine,
    task,
    workspace,
    changes,
    repair=None,
    expected_head=None,
    result=None,
    repo_name=None,
    patterns=None,
    batch_id=None,
):
    engine.process_barrier(task["track"]).require_clear()
    engine.check_claim(task)
    workspace = Path(workspace).resolve()
    prior = read(engine, task["track"], repo_name)
    validate_changes(changes)
    if prior and prior["phase"] in ACTIVE:
        recover(engine, task, workspace, repo_name)
        prior = read(engine, task["track"], repo_name)
    if (
        prior
        and prior["phase"] == "committed"
        and prior["intent"]["proposal"] == changes
        and prior["intent"]["workspace"] == str(workspace)
        and (
            expected_head in (None, prior["intent"]["before_head"])
            or (repo_name is not None and expected_head == prior["head"])
        )
        and (batch_id is None or prior["intent"].get("batch_id") == batch_id)
        and command(["git", "rev-parse", "HEAD"], workspace) == prior["head"]
        # A live merge is a new adoption; committed retries have cleared MERGE_HEAD.
        and not integration.merge_head(workspace)
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
        workspace, changes, patterns or engine.config["writable_patterns"], expected_head
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
        "repository": repo_name,
        "batch_id": batch_id,
        "task": task["id"],
        "attempt": task["attempt"],
        "generation": task["generation"],
        "workspace": str(workspace),
        "before_head": expected_head,
        "merge_head": merging,
        "proposal": changes,
        "files": files,
        "snapshot": snapshot(workspace),
        "outside_snapshot": _workspace_outside_snapshot(workspace, paths),
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
                workspace,
                changes,
                patterns or engine.config["writable_patterns"],
                expected_head,
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
        write_json(journal_path(engine, task["track"], repo_name), record)
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
    record = _ready_record(engine, task, workspace, record)
    _commit_and_adopt(engine, task, workspace, record)
