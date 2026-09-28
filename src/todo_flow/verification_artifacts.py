"""Record verification-created residue and reclaim only unchanged, attributed files.

The host must serialize capture with verification and call finish only after the
verification processes have stopped, including on a confirmed unsuccessful exit.
The state directory must live outside the checkout. Reclamation requires the
caller's existing delivery, ownership and process-quiescence checks; this module
does not authorize worktree removal. Incomplete observations preserve residue.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .adapters import command
from .maintenance import write_json
from .store import Conflict, fingerprint


def checkout_identity(workspace):
    path = Path(workspace).absolute()
    if path.is_symlink() or path.resolve() != path:
        raise Conflict("Checkout path changed or contains a symlink")
    info = path.stat()
    gitdir = Path(command(["git", "rev-parse", "--absolute-git-dir"], path)).resolve()
    gitinfo = gitdir.stat()
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "gitdir": str(gitdir),
        "gitDevice": gitinfo.st_dev,
        "gitInode": gitinfo.st_ino,
        "common": command(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], path),
    }


def _eligible(name):
    parts = Path(name).parts
    return bool(parts) and (
        parts[0] in (".venv", "dist", "build", ".ruff_cache")
        or any(part.endswith(".egg-info") for part in parts[:-1])
        or ("__pycache__" in parts[:-1] and parts[-1].endswith(".pyc"))
    )


def _stat_key(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def _file_stamp(workspace, name):
    relative = Path(name)
    if not relative.parts or relative.is_absolute() or {"..", ".git"}.intersection(relative.parts):
        raise Conflict("Invalid artifact path")
    path = workspace / relative
    for parent in path.parents:
        if parent == workspace:
            break
        if parent.is_symlink():
            raise Conflict("Artifact has a symlink ancestor: " + name)
    info = path.lstat()
    before = _stat_key(info)
    if stat.S_ISLNK(info.st_mode):
        digest = {"link": os.readlink(path)}
    elif stat.S_ISREG(info.st_mode):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if _stat_key(os.fstat(stream.fileno())) != before:
                raise Conflict("Artifact changed before inspection: " + name)
            digest = {"sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
            if _stat_key(os.fstat(stream.fileno())) != before:
                raise Conflict("Artifact changed during inspection: " + name)
    else:
        raise Conflict("Non-file checkout residue requires inspection: " + name)
    if _stat_key(path.lstat()) != before:
        raise Conflict("Artifact changed during inspection: " + name)
    return {"stat": before, **digest}


def _snapshot(workspace):
    names = set()
    for ignored in (False, True):
        args = ["git", "ls-files", "--others", "--exclude-standard", "-z"]
        if ignored:
            args.append("--ignored")
        # Preserve whitespace, newlines and symlink names exactly. command()
        # strips output, which is inappropriate for a NUL-delimited inventory.
        output = subprocess.run(
            args, cwd=workspace, capture_output=True, check=True, timeout=120
        ).stdout
        names.update(name for name in os.fsdecode(output).split("\0") if name)
    return {name: _file_stamp(workspace, name) for name in sorted(names)}


def manifest_path(directory, track, identity):
    return Path(directory) / "verification-artifacts" / (fingerprint([track, identity]) + ".json")


def _read_manifest(path, track, identity):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("track") != track
        or value.get("checkout") != identity
        or not isinstance(value.get("files"), dict)
    ):
        raise Conflict("Artifact evidence does not match this checkout")
    return value


class Capture:
    """Bracket one actual verifier execution; cache hits must not create captures."""

    def __init__(self, workspace, launch, argv):
        self.workspace = Path(workspace).absolute()
        self.identity = checkout_identity(self.workspace)
        self.path = manifest_path(launch["directory"], launch["track"], self.identity)
        self.before = _snapshot(self.workspace)
        prior = _read_manifest(self.path, launch["track"], self.identity)
        # External edits lose attribution before the verifier can overwrite them.
        self.owned = {
            name
            for name, stamp in prior.get("files", {}).items()
            if prior.get("phase") == "complete" and self.before.get(name) == stamp
        }
        self.record = {
            "version": 1,
            "track": launch["track"],
            "checkout": self.identity,
            "execution": {**launch, "directory": str(launch["directory"])},
            "command": list(argv),
            "head": command(["git", "rev-parse", "HEAD"], self.workspace),
            "phase": "running",
            "files": {},
        }
        write_json(self.path, self.record)

    def finish(self):
        """Complete attribution after confirmed exit, independently of test success."""
        if checkout_identity(self.workspace) != self.identity:
            raise Conflict("Checkout changed during verification")
        current = _read_manifest(self.path, self.record["track"], self.identity)
        if self.record["phase"] != "running" or current != self.record:
            raise Conflict("Artifact capture was completed or superseded")
        after = _snapshot(self.workspace)
        self.record.update(
            phase="complete",
            files={
                name: stamp
                for name, stamp in after.items()
                if _eligible(name) and (name not in self.before or name in self.owned)
            },
        )
        write_json(self.path, self.record)


def removable_files(directory, track, workspace):
    """Inspect all residue, refusing the entire operation if any file lacks proof."""
    workspace = Path(workspace).absolute()
    identity = checkout_identity(workspace)
    record = _read_manifest(manifest_path(directory, track, identity), track, identity)
    current = _snapshot(workspace)
    evidence = record.get("files", {}) if record.get("phase") == "complete" else {}
    unknown = [
        name
        for name, stamp in current.items()
        if not _eligible(name) or evidence.get(name) != stamp
    ]
    if unknown:
        raise Conflict("Unattributed or changed user files remain: " + ", ".join(unknown[:5]))
    return current


def reclaim(directory, track, workspace, *, dry_run=False):
    """Unlink proven files; retain the manifest so interrupted deletion can resume."""
    workspace = Path(workspace).absolute()
    identity = checkout_identity(workspace)
    files = removable_files(directory, track, workspace)
    if checkout_identity(workspace) != identity or _snapshot(workspace) != files:
        raise Conflict("Checkout residue changed before removal")
    if not dry_run:
        for name, stamp in files.items():
            if _file_stamp(workspace, name) != stamp:
                raise Conflict("Artifact changed before removal: " + name)
            (workspace / name).unlink()
    # Leave directories for the separately authorized Git worktree removal.
    # Never recursively delete by conventional directory name or follow links.
    return sorted(files)
