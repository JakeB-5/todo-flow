"""First-adoption checks for an Orca-created checkout.

Capture pins before creation, persist them in the creation intent, and validate
only after a separate show by create's worktree.id. The result is an observation,
not an ownership receipt. Engine must still fence and persist ownership before
registration. This module never selects a route or enables native sessions.
"""

from pathlib import Path
import re

from .orca_workspace import (
    WorkspaceExpectation,
    _git,
    _object,
    _path,
    _result,
    _text,
    validate_workspace,
)
from .workspace_creation import WorkspaceCreationBlocked


def _local(status):
    result = _result(status)
    runtime = _object(result.get("runtime"))
    if (
        _object(result.get("target")).get("kind") != "local"
        or runtime.get("reachable") is not True
        or runtime.get("state") != "ready"
    ):
        raise ValueError("Local Orca runtime is not ready")
    return _text(runtime.get("runtimeId"))


def _oid(value):
    if not isinstance(value, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise ValueError("A pinned commit ID is required")
    return value


def _inventory(root):
    branches = _git(root, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines()
    worktrees = {}
    # -z avoids Git's quoted-path syntax, including spaces and newlines in paths.
    for record in _git(root, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        if not record:
            continue
        fields = record.split("\0")
        if not fields[0].startswith("worktree "):
            raise ValueError("Unrecognized Git worktree inventory")
        path = Path(fields[0][len("worktree ") :])
        if not path.is_absolute():
            raise ValueError("Nonabsolute worktree inventory path")
        path = str(path.resolve())
        refs = [field[len("branch ") :] for field in fields if field.startswith("branch ")]
        if path in worktrees or len(refs) > 1:
            raise ValueError("Ambiguous Git worktree inventory")
        worktrees[path] = refs[0] if refs else None
    if not worktrees:
        raise ValueError("Missing Git worktree inventory")
    return sorted(branches), worktrees


def capture_creation_pins(status, repo_response, *, repo_path, base):
    """Read host anchors and preexisting resources while holding metadata lock."""
    try:
        runtime = _local(status)
        root = _path(repo_path)
        repo = _object(_result(repo_response).get("repo"))
        if repo.get("kind") != "git" or _path(repo.get("path")) != root:
            raise ValueError("Repository does not match the configured root")
        if _path(_git(root, "rev-parse", "--show-toplevel")) != root:
            raise ValueError("Configured repository is not a checkout root")
        base = _oid(base)
        if _git(root, "rev-parse", base + "^{commit}") != base:
            raise ValueError("Base is not a commit")
        common = _path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        branches, worktrees = _inventory(root)
        return {
            "version": 1,
            "host": "local",
            "runtime_id": runtime,
            "repo_id": _text(repo.get("id")),
            "repo_path": str(root),
            "common_dir": str(common),
            "base": base,
            "branches": branches,
            "worktrees": worktrees,
        }
    except Exception as error:
        raise WorkspaceCreationBlocked("Cannot pin workspace creation") from error


def validate_creation_pins(pins):
    """Reject unsupported or incomplete persisted anchors without upgrading them."""
    pins = _object(pins)
    required = {
        "version",
        "host",
        "runtime_id",
        "repo_id",
        "repo_path",
        "common_dir",
        "base",
        "branches",
        "worktrees",
    }
    if set(pins) != required or type(pins["version"]) is not int or pins["version"] != 1:
        raise ValueError("Unsupported creation pins")
    if pins["host"] != "local":
        raise ValueError("Creation host is not local")
    for key in ("runtime_id", "repo_id", "repo_path", "common_dir"):
        _text(pins[key])
    for key in ("repo_path", "common_dir"):
        if not Path(pins[key]).is_absolute():
            raise ValueError("Creation anchor is not absolute")
    _oid(pins["base"])
    branches = pins["branches"]
    if (
        not isinstance(branches, list)
        or not all(isinstance(ref, str) and ref.startswith("refs/heads/") for ref in branches)
        or len(set(branches)) != len(branches)
    ):
        raise ValueError("Invalid branch inventory")
    worktrees = _object(pins["worktrees"])
    if not worktrees or any(
        not isinstance(path, str)
        or not Path(path).is_absolute()
        or (ref is not None and ref not in branches)
        for path, ref in worktrees.items()
    ):
        raise ValueError("Invalid worktree inventory")


def creation_argv(cli, pins, name):
    """Build the public create command; no agent, prompt or setup hooks."""
    validate_creation_pins(pins)
    return [
        _text(cli),
        "worktree",
        "create",
        "--repo",
        "id:" + pins["repo_id"],
        "--name",
        _text(name),
        "--base-branch",
        pins["base"],
        "--setup",
        "skip",
        "--no-parent",
        "--json",
    ]


def validate_created_workspace(status, repo_response, response, shown, *, pins):
    """Validate a new candidate against pre-create anchors and actual local Git.

    The caller obtains shown via show_created_workspace, never by reusing create.
    Response-derived identity fields alone cannot authorize first adoption: the
    new branch and path must be absent from the prior inventory, present together
    now, and independently match the pinned repository and exact creation base.
    """
    try:
        validate_creation_pins(pins)
        root = _path(pins["repo_path"])
        common = _path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        if str(common) != pins["common_dir"]:
            raise ValueError("Pinned Git repository changed")
        created = _object(_result(response).get("worktree"))
        worktree = _object(_result(shown).get("worktree"))
        worktree_id = _text(created.get("id"))
        if worktree.get("id") != worktree_id:
            raise ValueError("Show does not identify the created resource")
        path = str(_path(worktree.get("path")))
        branch = _text(worktree.get("branch"))
        if not branch.startswith("refs/heads/") or branch == "refs/heads/":
            raise ValueError("Candidate has no local branch")
        if path in pins["worktrees"] or branch in pins["branches"]:
            raise ValueError("Create returned a preexisting checkout or branch")
        branches, worktrees = _inventory(root)
        if branch not in branches or worktrees.get(path) != branch:
            raise ValueError("Candidate is not registered in the pinned Git repository")
        # Public create can include more than id. If present these observations
        # must agree with the separate show; missing fields are not invented.
        for key in (
            "path",
            "branch",
            "head",
            "repoId",
            "hostId",
            "instanceId",
            "identity",
            "projectId",
            "projectHostSetupId",
            "isBare",
            "isMainWorktree",
        ):
            if key in created and created[key] != worktree.get(key):
                raise ValueError("Create and show disagree")
        identity = _object(worktree.get("identity"))
        expected = WorkspaceExpectation(
            worktree_id=worktree_id,
            identity_key=_text(identity.get("key")),
            instance_id=_text(worktree.get("instanceId")),
            repo_id=pins["repo_id"],
            project_id=_text(worktree.get("projectId")),
            setup_id=_text(worktree.get("projectHostSetupId")),
            repo_path=pins["repo_path"],
            path=path,
            branch=branch[len("refs/heads/") :],
            base=pins["base"],
            head=pins["base"],
        )
        return validate_workspace(status, repo_response, shown, expected=expected)
    except Exception as error:
        raise WorkspaceCreationBlocked(
            "New workspace failed independent adoption checks"
        ) from error
