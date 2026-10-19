"""Fenced Orca checkout creation and recovery, called under Engine's Git lock."""

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from time import monotonic, sleep
import uuid

from .adapters import command
from .launchers import select_launcher
from .orca_adoption import capture_creation_pins, creation_argv, validate_created_workspace
from .orca_workspace import (
    WorkspaceExpectation,
    WorkspaceHeadLag,
    show_created_workspace,
    validate_workspace,
)
from .store import Conflict
from .workspace_creation import WorkspaceCreationBlocked, WorkspaceCreationGate, _write_exclusive


# Admission window for new observations, not a subprocess wall-time deadline.
# At most 31 probes, one second apart. An admitted probe can take another
# 3 * 30s (Orca) + 7 * 10s (Git), so subprocess/sleep time is bounded by 190s
# with the monotonic clock, excluding local I/O and scheduling delays.
# This is a bounded recovery policy, not a measured Orca convergence guarantee.
_HEAD_RECHECK_SECONDS = 30.0
_HEAD_RECHECK_INTERVAL = 1.0
_HEAD_RECHECK_LIMIT = 31


def _digest(value):
    return hashlib.sha256(json.dumps(value, allow_nan=False, sort_keys=True).encode()).hexdigest()


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8_000_000:
                raise ValueError("Invalid ownership evidence file")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Invalid ownership evidence")
        return value
    except (OSError, ValueError) as error:
        raise WorkspaceCreationBlocked(f"Cannot read ownership evidence: {path}") from error


def _call(cli, args, root):
    result = subprocess.run(
        [cli, *args], cwd=root, capture_output=True, text=True, timeout=30, check=True
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise WorkspaceCreationBlocked("Orca did not confirm the requested operation")
    return payload


def _claim(engine, task):
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)


def receipt_path(gate):
    return gate.intent.with_suffix(".ownership.json")


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _expectation(observation, head):
    return WorkspaceExpectation(
        worktree_id=observation["id"],
        identity_key=observation["identity"]["key"],
        instance_id=observation["instanceId"],
        repo_id=observation["repoId"],
        project_id=observation["projectId"],
        setup_id=observation["projectHostSetupId"],
        repo_path=observation["repo_path"],
        path=observation["path"],
        branch=observation["branch"].removeprefix("refs/heads/"),
        base=observation["base"],
        head=head,
    )


def _register(engine, task, observation):
    track = engine.store.track(task["track"])
    branch = observation["branch"].removeprefix("refs/heads/")
    if track["branch"] is not None or track["workspace"] is not None:
        if track["branch"] != branch or track["workspace"] != observation["path"]:
            raise WorkspaceCreationBlocked("Ownership disagrees with the registered candidate")
    engine.update(task, branch=branch, workspace=observation["path"], head=observation["head"])
    return Path(observation["path"])


def ownership(store, track, root, *, connection=None):
    """Read and bind creation evidence without requiring a live checkout or claim."""
    gate = WorkspaceCreationGate(store.path, track["id"])
    try:
        receipt, intent, response = (
            _read(path) for path in (receipt_path(gate), gate.intent, gate.response)
        )
        if (
            type(receipt.get("version")) is not int
            or receipt["version"] != 1
            or intent.get("version") != 2
            or response.get("version") != 2
            or receipt.get("intent_sha256") != _digest(intent)
            or receipt.get("response_sha256") != _digest(response)
            or response.get("intent_sha256") != _digest(intent)
            or response.get("claim") != intent.get("claim")
            or receipt.get("track") != track["id"]
            or intent.get("track") != track["id"]
            or intent.get("claim", {}).get("track") != track["id"]
            or receipt.get("request") != track["request"]
            or intent.get("request") != track["request"]
            or response.get("request") != track["request"]
            or receipt.get("revision") != track["revision"]
            or intent["claim"].get("input_revision") != track["revision"]
        ):
            raise ValueError("Ownership evidence binding mismatch")
        origin = intent["claim"]
        with nullcontext(connection) if connection is not None else store.connect() as connection:
            row = connection.execute(
                "SELECT task,generation FROM attempts WHERE id=?", (origin["attempt"],)
            ).fetchone()
        if row is None or row["task"] != origin["id"] or row["generation"] != origin["generation"]:
            raise ValueError("Ownership origin attempt is missing")
        pins = intent["creation_pins"]
        if (
            intent["repo"] != str(Path(root).resolve())
            or pins["repo_path"] != intent["repo"]
            or intent["base"] != pins["base"]
            or intent["argv"] != creation_argv(intent["argv"][0], pins, intent["argv"][6])
        ):
            raise ValueError("Ownership repository or creation command changed")
        old = receipt["observation"]
        if (
            old["id"] != response["response"]["result"]["worktree"]["id"]
            or old["repo_path"] != pins["repo_path"]
            or old["common_dir"] != pins["common_dir"]
            or old["repoId"] != pins["repo_id"]
            or old["base"] != pins["base"]
            or old["head"] != pins["base"]
        ):
            raise ValueError("Ownership observation does not match creation anchors")
        return receipt, intent
    except WorkspaceCreationBlocked:
        raise
    except Exception as error:
        raise WorkspaceCreationBlocked("Owned workspace requires reconciliation") from error


def _recover(engine, task, gate, *, proposal_recovery=False):
    try:
        track = engine.store.track(task["track"])
        receipt, intent = ownership(engine.store, track, engine.root)
        old = receipt["observation"]
        pins = intent["creation_pins"]
        head = track["head"] if track["branch"] else pins["base"]
        if proposal_recovery:
            # Validate live ownership without adopting an interrupted commit's HEAD.
            # The proposal journal separately proves its exact before/after boundary.
            head = command(["git", "rev-parse", "HEAD"], old["path"])
        expected = _expectation(old, head)
        cli = intent["argv"][0]

        def check_owner():
            _claim(engine, task)
            current = engine.store.track(task["track"])
            keys = ("request", "revision", "branch", "workspace", "head")
            if any(current[key] != track[key] for key in keys):
                raise WorkspaceCreationBlocked(
                    "Owned workspace registration changed during recovery"
                )
            if current["branch"] is not None or current["workspace"] is not None:
                if current["branch"] != expected.branch or current["workspace"] != expected.path:
                    raise WorkspaceCreationBlocked(
                        "Ownership disagrees with the registered candidate"
                    )
            if ownership(engine.store, current, engine.root) != (receipt, intent):
                raise WorkspaceCreationBlocked("Workspace ownership changed during recovery")

        def read(args):
            _claim(engine, task)
            result = _call(cli, args, engine.root)
            _claim(engine, task)
            return result

        deadline = monotonic() + _HEAD_RECHECK_SECONDS
        last_lag = None
        observations = 0
        for _ in range(_HEAD_RECHECK_LIMIT):
            if last_lag is not None and monotonic() >= deadline:
                break
            check_owner()
            observations += 1
            try:
                checked = validate_workspace(
                    read(["status", "--json"]),
                    read(["repo", "show", "--repo", "id:" + pins["repo_id"], "--json"]),
                    read(["worktree", "show", "--worktree", "id:" + old["id"], "--json"]),
                    expected=expected,
                    expected_common_dir=pins["common_dir"],
                )
            except WorkspaceHeadLag as error:
                last_lag = error
            else:
                check_owner()
                if proposal_recovery:
                    return Path(checked["path"])
                return _register(engine, task, checked)
            check_owner()
            remaining = deadline - monotonic()
            if remaining <= 0 or observations == _HEAD_RECHECK_LIMIT:
                break
            sleep(min(_HEAD_RECHECK_INTERVAL, remaining))
        raise WorkspaceCreationBlocked(
            f"Owned workspace HEAD observation did not converge after {observations} probes: "
            f"{last_lag}"
        ) from last_lag
    except (WorkspaceCreationBlocked, Conflict):
        raise
    except Exception as error:
        raise WorkspaceCreationBlocked("Owned workspace requires reconciliation") from error


def ensure(engine, task, *, proposal_recovery=False):
    """Return a managed checkout, or None for a pre-creation compatibility route.

    Caller holds Git metadata lock. No fallback is possible after a create intent.
    Existing Git candidates remain registered at their original branch and path.
    """
    _claim(engine, task)
    gate = WorkspaceCreationGate(engine.store.path, task["track"])
    if _exists(receipt_path(gate)):
        return _recover(engine, task, gate, proposal_recovery=proposal_recovery)
    gate.require_clear()
    if proposal_recovery:
        return None
    track = engine.store.track(task["track"])
    if track["branch"] or track["workspace"]:
        return None
    # Workspace ownership is provider-independent. Role routing may select a
    # different provider from the project default, including future adapters.
    if engine.config.get("worker_launcher", "auto") not in {"auto", "orca"}:
        return None
    launcher = select_launcher(engine.config, str(engine.root))
    advertised = launcher.get("selection", {}).get("orca", {}).get("advertised", {})
    if launcher["backend"] != "orca" or not advertised.get("managed_agent_worktree"):
        return None
    cli = launcher["cli"]

    def read(args):
        return _call(cli, args, engine.root)

    shown = read(["worktree", "show", "--worktree", "path:" + str(engine.root), "--json"])
    repo_id = shown["result"]["worktree"]["repoId"]
    repo = read(["repo", "show", "--repo", "id:" + repo_id, "--json"])
    status = read(["status", "--json"])
    command(["git", "fetch", "origin", engine.config["base"]], engine.root)
    base = command(
        ["git", "rev-parse", "origin/" + engine.config["base"] + "^{commit}"], engine.root
    )
    pins = capture_creation_pins(status, repo, repo_path=str(engine.root), base=base)
    argv = creation_argv(cli, pins, "todo-" + task["track"] + "-" + uuid.uuid4().hex[:12])
    response = gate.create_once(
        task,
        request=track["request"],
        repo=str(engine.root.resolve()),
        base=base,
        argv=argv,
        assert_claim=lambda: _claim(engine, task),
        create=lambda args: _call(args[0], args[1:], engine.root),
        creation_pins=pins,
    )
    checked = validate_created_workspace(
        read(["status", "--json"]),
        read(["repo", "show", "--repo", "id:" + repo_id, "--json"]),
        response,
        show_created_workspace(response, read=read),
        pins=pins,
    )
    with engine.store.transaction() as connection:
        engine.store.assert_claim(connection, task)
        _write_exclusive(
            receipt_path(gate),
            {
                "version": 1,
                "track": task["track"],
                "request": track["request"],
                "revision": track["revision"],
                "intent_sha256": _digest(_read(gate.intent)),
                "response_sha256": _digest(_read(gate.response)),
                "observation": checked,
            },
        )
    _claim(engine, task)
    return _register(engine, task, checked)
