"""Preserve conflicted integrations before narrowly authorizing their removal."""

import base64
import json
import os
from pathlib import Path
import subprocess

from .adapters import command
from .maintenance import write_json
from .store import Conflict, fingerprint
from .verification_artifacts import _file_stamp, checkout_identity


def evidence_path(directory, workspace):
    return Path(directory) / "obsolete-integrations" / (fingerprint(str(workspace)) + ".json")


def read_evidence(directory, workspace):
    path = evidence_path(directory, workspace)
    return json.loads(path.read_text()) if path.exists() else None


def git_bytes(workspace, *args):
    return subprocess.check_output(["git", *args], cwd=workspace)


def encoded(data):
    return base64.b64encode(data).decode("ascii")


def observe(workspace):
    """Capture a reconstructable logical index, its blobs and tracked working files.

    Logical entries avoid incidental index stat-cache refreshes. File stamps and
    a second complete observation reject edits during capture or after capture.
    Untracked and ignored residue remains subject to the ordinary cleanup gate.
    """
    workspace = Path(workspace)
    identity = checkout_identity(workspace)
    index = os.fsdecode(git_bytes(workspace, "ls-files", "--stage", "-z"))
    names, blobs = set(), {}
    for row in index.split("\0"):
        if not row:
            continue
        metadata, name = row.split("\t", 1)
        mode, blob, _stage = metadata.split()
        if mode == "160000":
            raise Conflict("Preserve conflicted integrations containing submodules")
        names.add(name)
        if blob not in blobs:
            blobs[blob] = encoded(git_bytes(workspace, "cat-file", "blob", blob))
    files = {}
    for name in sorted(names):
        path = workspace / name
        if not os.path.lexists(path):
            files[name] = None
            continue
        stamp = _file_stamp(workspace, name)
        data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        if _file_stamp(workspace, name) != stamp:
            raise Conflict("Integration file changed during evidence capture")
        files[name] = {"stamp": stamp, "data": encoded(data)}
    metadata = {}
    for name in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE"):
        path = Path(identity["gitdir"]) / name
        metadata[name] = encoded(path.read_bytes()) if path.exists() else None
    return {
        "checkout": identity,
        "head": command(["git", "rev-parse", "HEAD"], workspace),
        "branch": command(["git", "rev-parse", "--symbolic-full-name", "HEAD"], workspace),
        "merge": metadata,
        "index": index,
        "blobs": blobs,
        "files": files,
    }


def capture(store, track, workspace, base, candidate):
    observed = observe(workspace)
    if (
        observed["head"] != base
        or observed["branch"] != "HEAD"
        or observed["merge"]["MERGE_HEAD"] != encoded((candidate + "\n").encode())
        or observe(workspace) != observed
    ):
        raise Conflict("Original integration changed before conflict evidence was saved")
    record = {
        "version": 1,
        "track": track["id"],
        "request": track["request"],
        "revision": track["revision"],
        "base": base,
        "candidate": candidate,
        "observed": observed,
    }
    path = evidence_path(store.path, workspace)
    previous = read_evidence(store.path, workspace)
    if previous is not None and previous != record:
        raise Conflict("Original integration evidence already exists and differs")
    write_json(path, record)
    if read_evidence(store.path, workspace) != record:
        raise Conflict("Original integration evidence persistence is unconfirmed")


def resolved(store, track, workspace, repair, head):
    if not repair.get("obsolete_evidence"):
        return
    record = read_evidence(store.path, repair["integration"])
    if (
        not record
        or record["track"] != track["id"]
        or record["request"] != track["request"]
        or record["revision"] != track["revision"]
        or record["candidate"] != repair["candidate"]
    ):
        raise Conflict("Original integration evidence is missing or mismatched")
    parents = command(["git", "show", "-s", "--format=%P", head], workspace).split()
    if parents != [repair["candidate"], repair["base"]]:
        raise Conflict("Repair commit does not match the prepared merge parents")
    resolution = {"head": head, "parents": parents}
    if record.get("resolution", resolution) != resolution:
        raise Conflict("Original integration already has a different resolution")
    record["resolution"] = resolution
    write_json(evidence_path(store.path, repair["integration"]), record)
    if read_evidence(store.path, repair["integration"]) != record:
        raise Conflict("Integration resolution persistence is unconfirmed")


def removable(store, track, workspace):
    """Caller must first enforce ownership, quiescence, landing and cleared triage."""
    workspace = Path(workspace)
    if workspace.parent != store.path / "integrations":
        return None
    record = read_evidence(store.path, workspace)
    if record is None:
        return None
    if (
        record.get("version") != 1
        or record.get("track") != track["id"]
        or record.get("request") != track["request"]
        or record.get("revision") != track["revision"]
        or not record.get("resolution")
    ):
        raise Conflict("Conflicted integration has no matching completed repair")
    observed = record["observed"]
    resolution = record["resolution"]
    if (
        observed["head"] != record["base"]
        or observed["branch"] != "HEAD"
        or observed["merge"]["MERGE_HEAD"] != encoded((record["candidate"] + "\n").encode())
        or resolution["parents"][0] != record["candidate"]
        or command(["git", "show", "-s", "--format=%P", resolution["head"]], workspace).split()
        != resolution["parents"]
    ):
        raise Conflict("Integration parent evidence does not match its resolution")
    for parent in (record["base"], record["candidate"]):
        command(["git", "merge-base", "--is-ancestor", parent, resolution["head"]], workspace)
    command(["git", "merge-base", "--is-ancestor", resolution["head"], track["head"]], workspace)
    if observe(workspace) != observed:
        raise Conflict("Original integration identity, parents, index or files changed")
    return {"path": str(evidence_path(store.path, workspace)), "digest": fingerprint(record)}
