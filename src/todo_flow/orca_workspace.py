"""Validate public Orca workspace observations against host-pinned expectations.

This module does not authorize registration, select a route or create resources.
The caller must hold the metadata lock and a current claim, and obtain expectations
from durable ownership evidence, never from the response being validated. For first
registration head must be the creation base; for an owned candidate it is the current
host-pinned HEAD. Engine recovery and ownership receipt persistence remain separate.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess

from .workspace_creation import WorkspaceCreationBlocked


@dataclass(frozen=True)
class WorkspaceExpectation:
    worktree_id: str
    identity_key: str
    instance_id: str
    repo_id: str
    project_id: str
    setup_id: str
    repo_path: str
    path: str
    branch: str
    base: str
    head: str


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("Expected a nonempty string")
    return value


def _result(payload):
    payload = _object(payload)
    if payload.get("ok") is not True:
        raise ValueError("Orca did not return success")
    return _object(payload.get("result"))


def _path(value):
    path = Path(_text(value))
    if not path.is_absolute():
        raise ValueError("Expected an absolute path")
    return path.resolve(strict=True)


def _git(path, *args):
    # Do not let the coordinator's Git routing environment select another checkout.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def show_created_workspace(response, *, read):
    """Read show using only the documented create result's full worktree.id.

    read accepts public CLI argv excluding the executable and returns decoded JSON.
    It must perform one read, without falling back to create or another backend.
    A saved create response is evidence, not durable ownership authorization.
    """
    try:
        worktree_id = _text(_object(_result(response).get("worktree")).get("id"))
        shown = read(["worktree", "show", "--worktree", "id:" + worktree_id, "--json"])
        worktree = _object(_result(shown).get("worktree"))
        if worktree.get("id") != worktree_id:
            raise ValueError("Show returned another worktree")
        return shown
    except Exception as error:
        raise WorkspaceCreationBlocked("Created workspace requires reconciliation") from error


def validate_workspace(status, repo_response, show_response, *, expected):
    """Return checked observation only; no registration or ownership is implied."""
    try:
        for value in vars(expected).values():
            _text(value)
        for oid in (expected.base, expected.head):
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None:
                raise ValueError("Base and HEAD must be pinned object IDs")
        status = _result(status)
        runtime = _object(status.get("runtime"))
        if (
            _object(status.get("target")).get("kind") != "local"
            or runtime.get("reachable") is not True
            or runtime.get("state") != "ready"
        ):
            raise ValueError("Local Orca runtime is not ready")
        _text(runtime.get("runtimeId"))
        repo = _object(_result(repo_response).get("repo"))
        worktree = _object(_result(show_response).get("worktree"))
        identity = _object(worktree.get("identity"))
        nested = _object(worktree.get("git"))
        fields = {
            "id": expected.worktree_id,
            "instanceId": expected.instance_id,
            "repoId": expected.repo_id,
            "projectId": expected.project_id,
            "projectHostSetupId": expected.setup_id,
            "hostId": "local",
            "branch": "refs/heads/" + expected.branch,
            "head": expected.head,
        }
        if any(worktree.get(key) != value for key, value in fields.items()):
            raise ValueError("Workspace identity or revision mismatch")
        if (
            identity.get("key") != expected.identity_key
            or identity.get("instanceId") != expected.instance_id
            or identity.get("executionHostId") != "local"
        ):
            raise ValueError("Workspace execution identity mismatch")
        if repo.get("id") != expected.repo_id or repo.get("kind") != "git":
            raise ValueError("Repository identity mismatch")
        root, workspace = _path(expected.repo_path), _path(expected.path)
        if _path(repo.get("path")) != root or _path(worktree.get("path")) != workspace:
            raise ValueError("Workspace or repository path mismatch")
        if workspace == root:
            raise ValueError("A separate candidate checkout is required")
        for key in ("path", "head", "branch", "isBare", "isMainWorktree"):
            if key not in nested or nested[key] != worktree.get(key):
                raise ValueError("Nested Git observation mismatch")
        if worktree.get("isBare") is not False or worktree.get("isMainWorktree") is not False:
            raise ValueError("Expected a non-main, non-bare worktree")
        common = _path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        candidate_common = _path(
            _git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        if common != candidate_common:
            raise ValueError("Candidate belongs to another Git repository")
        if _path(_git(workspace, "rev-parse", "--show-toplevel")) != workspace:
            raise ValueError("Candidate path is not the checkout root")
        if _git(workspace, "symbolic-ref", "--quiet", "HEAD") != fields["branch"]:
            raise ValueError("Actual Git branch mismatch")
        if _git(workspace, "rev-parse", "HEAD") != expected.head:
            raise ValueError("Actual Git HEAD mismatch")
        if _git(workspace, "rev-parse", expected.base + "^{commit}") != expected.base:
            raise ValueError("Creation base is not a commit")
        _git(workspace, "merge-base", "--is-ancestor", expected.base, expected.head)
        return {
            **fields,
            "identity": dict(identity),
            "path": str(workspace),
            "repo_path": str(root),
            "common_dir": str(common),
            "base": expected.base,
            "runtime_id": runtime["runtimeId"],
        }
    except Exception as error:
        raise WorkspaceCreationBlocked("Workspace observation failed validation") from error
