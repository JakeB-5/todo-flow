"""Validate and assemble file proposals without mutating the checkout."""

import hashlib
import re
from pathlib import Path, PurePosixPath

from .adapters import permitted
from .store import Conflict


FULL_CHANGE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
    "additionalProperties": False,
}
REPLACE_CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "format": {"type": "string", "enum": ["replace-v1"]},
        "base_head": {"type": "string"},
        "sha256": {"type": "string"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["old", "new"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["path", "format", "base_head", "sha256", "edits"],
    "additionalProperties": False,
}
CHANGE_SCHEMA = {"anyOf": [FULL_CHANGE_SCHEMA, REPLACE_CHANGE_SCHEMA]}


def utf8(value):
    if not isinstance(value, str):
        raise ValueError("Change fields must be UTF-8 strings")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("Invalid UTF-8 change field") from error


def validate_changes(changes):
    """Shared by worker decoding and the host's direct application boundary."""
    if not isinstance(changes, list):
        raise ValueError("Changes must be an array")
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Invalid change")
        if "format" not in change:
            if set(change) != {"path", "content"}:
                raise ValueError("Invalid full-file change")
            utf8(change["path"])
            utf8(change["content"])
            continue
        if change["format"] != "replace-v1":
            raise ValueError("Unsupported change format")
        if set(change) != {"path", "format", "base_head", "sha256", "edits"}:
            raise ValueError("Invalid replace-v1 change")
        for key in ("path", "base_head", "sha256"):
            utf8(change[key])
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", change["base_head"]):
            raise ValueError("replace-v1 requires a full original HEAD")
        if not re.fullmatch(r"[0-9a-f]{64}", change["sha256"]):
            raise ValueError("replace-v1 requires a lowercase SHA-256 digest")
        if not isinstance(change["edits"], list) or not change["edits"]:
            raise ValueError("replace-v1 requires at least one edit")
        for edit in change["edits"]:
            if not isinstance(edit, dict) or set(edit) != {"old", "new"}:
                raise ValueError("Invalid replace-v1 edit")
            if not utf8(edit["old"]):
                raise ValueError("replace-v1 old must not be empty")
            utf8(edit["new"])


def replace_content(original, change, expected_head):
    if change["base_head"] != expected_head:
        raise Conflict("replace-v1 original HEAD does not match worker context")
    if hashlib.sha256(original).hexdigest() != change["sha256"]:
        raise Conflict("replace-v1 original SHA-256 changed: " + change["path"])
    try:
        original.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise Conflict("replace-v1 requires an existing UTF-8 file") from error
    ranges = []
    for edit in change["edits"]:
        old = utf8(edit["old"])
        start = original.find(old)
        # Search at start + 1 so overlapping occurrences also count as ambiguous.
        if start < 0 or original.find(old, start + 1) >= 0:
            raise Conflict("replace-v1 old must match exactly once: " + change["path"])
        ranges.append((start, start + len(old), utf8(edit["new"])))
    ranges.sort(key=lambda item: item[0])
    result = []
    end = 0
    for start, stop, replacement in ranges:
        if start < end:
            raise Conflict("Overlapping replace-v1 edits: " + change["path"])
        result.extend((original[end:start], replacement))
        end = stop
    result.append(original[end:])
    return b"".join(result).decode("utf-8")


def prepare_changes(workspace, changes, patterns, expected_head):
    """Return full UTF-8 contents after checking every path and original range."""
    validate_changes(changes)
    workspace = Path(workspace).resolve(strict=True)
    names = set()
    for change in changes:
        name = change["path"]
        path = PurePosixPath(name)
        if "\x00" in name or name != path.as_posix() or not permitted(name, patterns):
            raise Conflict("File is outside the normalized authorized write surface: " + name)
        if name in names:
            raise Conflict("Duplicate file paths in result")
        names.add(name)
    for name in names:
        path = PurePosixPath(name)
        if any(parent.as_posix() in names for parent in path.parents):
            raise Conflict("Proposal contains ancestor and descendant file paths")
        target = workspace / name
        if target.is_symlink() or not target.resolve().is_relative_to(workspace):
            raise Conflict("Symlink/path escape")
        if target.exists() and not target.is_file():
            raise Conflict("Proposal target is not a regular file: " + name)
        for parent in target.parents:
            if parent == workspace:
                break
            if parent.is_symlink():
                raise Conflict("Symlink ancestor")
            if parent.exists() and not parent.is_dir():
                raise Conflict("Proposal ancestor is not a directory: " + name)
    prepared = []
    for change in changes:
        name = change["path"]
        if "format" in change:
            target = workspace / name
            if not target.is_file():
                raise Conflict("replace-v1 requires an existing regular file: " + name)
            content = replace_content(target.read_bytes(), change, expected_head)
        else:
            content = change["content"]
        prepared.append({"path": name, "content": content})
    return prepared
