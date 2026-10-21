"""Optional condition references: byte integrity is not semantic correctness."""

import copy
import hashlib
import json
import os
from pathlib import Path

from . import repositories, verification_logs as logs, verification_outcomes
from .store import Conflict


LOG_REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "integer"},
        **{key: {"type": "string"} for key in ("track", "attempt", "execution", "manifest")},
    },
    "required": ["version", "track", "attempt", "execution", "manifest"],
    "additionalProperties": False,
}
REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "integer"},
        "conditionId": {"type": "string"},
        "documentRevision": {"type": "integer"},
        "head": {"type": "string"},
        "repository": {"type": "string"},
        "logReference": LOG_REFERENCE_SCHEMA,
        "stream": {"type": "string", "enum": ["stdout", "stderr"]},
        "path": {"type": "string"},
        "sha256": {"type": "string"},
    },
    "required": [
        "version",
        "conditionId",
        "documentRevision",
        "head",
        "logReference",
        "stream",
        "path",
        "sha256",
    ],
    "additionalProperties": False,
}


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def artifact(directory, reference, stream):
    """Hash the sealed original, checking both its receipt and actual termination."""
    if stream not in logs.STREAMS:
        raise ValueError("Unknown evidence stream")
    record = logs._load(directory, reference)
    termination = logs._termination(directory, reference)
    if (
        record.get("phase") != "closed"
        or record.get("complete") is not True
        or termination.get("state") != "confirmed"
        or termination.get("completion") != "leader-exited"
        or termination.get("returncode") != 0
    ):
        raise ValueError("Evidence requires sealed output and confirmed normal termination")
    path = Path(reference["manifest"]).parent / (stream + ".log")
    digest = hashlib.sha256()
    with logs._open(directory, path) as source:
        before = logs._stamp(os.fstat(source.fileno()))
        if record["files"].get(stream) != before:
            raise ValueError("Evidence original changed after sealing")
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        after = logs._stamp(os.fstat(source.fileno()))
    with logs._open(directory, path) as current:
        if before != after or before != logs._stamp(os.fstat(current.fileno())):
            raise ValueError("Evidence original changed while reading")
    if logs._load(directory, reference) != record:
        raise ValueError("Evidence receipt changed while reading")
    return {"stream": stream, "path": str(path), "sha256": digest.hexdigest()}


def _checks(track, config=None):
    try:
        verification = _decode(track.get("verification")) or {}
    except (ValueError, TypeError):
        return []
    if not isinstance(verification, dict):
        return []
    if verification.get("head") != track["head"]:
        return []
    records = verification.get("repositories")
    if isinstance(config, dict) and repositories.multi(config):
        if not isinstance(records, dict):
            return []
        try:
            names = [row["name"] for row in repositories.specs(config)]
            states = repositories.all_states(track, config)
        except (Conflict, ValueError, TypeError, KeyError):
            return []
        heads = verification.get("heads")
        if (
            len(names) < 2
            or set(records) != set(names)
            or not isinstance(heads, dict)
            or set(heads) != set(names)
            or heads.get(names[0]) != track["head"]
            or not verification_outcomes.passed(verification)
        ):
            return []
        result = []
        for repository in names:
            record = records.get(repository)
            if not isinstance(record, dict) or not verification_outcomes.passed(record):
                return []
            current_head = states[repository]["head"]
            if record.get("head") != heads.get(repository) or heads[repository] != current_head:
                return []
            checks = record.get("checks", [])
            if not isinstance(checks, list) or any(not isinstance(row, dict) for row in checks):
                return []
            result.extend((repository, heads[repository], check) for check in checks)
        return result
    if isinstance(records, dict):
        return []
    return [
        (None, track["head"], check)
        for check in verification.get("checks", [])
        if check.get("ok") is True
        and isinstance(check.get("logReference"), dict)
        and check["logReference"].get("track") == track["id"]
    ]


def available(directory, track, config=None):
    """Offer unassigned artifacts; a reviewer must explicitly select a condition."""
    result = []
    for repository, candidate_head, check in _checks(track, config):
        reference = check["logReference"]
        for stream in logs.STREAMS:
            try:
                value = artifact(directory, reference, stream)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            result.append(
                {
                    "version": 1,
                    "documentRevision": track["revision"],
                    "head": candidate_head,
                    **({"repository": repository} if repository else {}),
                    "logReference": copy.deepcopy(reference),
                    **value,
                }
            )
    return result


def validate(directory, track, condition, reference, config=None):
    """Reject incomplete, stale, foreign or changed explicit references."""
    required = set(REFERENCE_SCHEMA["required"])
    allowed = required | {"repository"}
    if (
        not isinstance(reference, dict)
        or not required <= set(reference)
        or not set(reference) <= allowed
        or type(reference.get("version")) is not int
        or reference["version"] != 1
    ):
        raise ValueError("Incomplete or unsupported condition evidence reference")
    document = _decode(track["document"])
    if (
        condition not in {row["id"] for row in document["conditions"]}
        or reference["conditionId"] != condition
        or type(reference["documentRevision"]) is not int
        or reference["documentRevision"] != track["revision"]
    ):
        raise ValueError("Evidence condition, document revision or candidate does not match")
    repository = reference.get("repository")
    if not isinstance(reference["head"], str):
        raise ValueError("Evidence candidate HEAD is invalid")
    if repository is None:
        expected_head = track["head"]
    else:
        verification = _decode(track.get("verification")) or {}
        records = verification.get("repositories")
        if not isinstance(repository, str) or not isinstance(records, dict):
            raise ValueError("Evidence repository does not belong to the current candidate")
        record = records.get(repository)
        expected_head = record.get("head") if isinstance(record, dict) else None
    if reference["head"] != expected_head:
        raise ValueError("Evidence candidate HEAD does not match the current repository")
    if not any(
        reference["logReference"] == check["logReference"]
        and repository == name
        and reference["head"] == candidate_head
        for name, candidate_head, check in _checks(track, config)
    ):
        raise ValueError("Evidence does not belong to a recorded candidate verification execution")
    observed = artifact(directory, reference["logReference"], reference["stream"])
    if any(reference[key] != observed[key] for key in ("stream", "path", "sha256")):
        raise ValueError("Evidence artifact path or SHA-256 does not match the sealed original")


def inspect_row(directory, track, row, config=None):
    """Never trust a persisted integrity label; recompute it from current originals."""
    references = row.get("evidenceRefs")
    if references is None or references == []:
        return {"status": "prose-only"}
    try:
        if not isinstance(references, list):
            raise ValueError("evidenceRefs must be an array")
        for reference in references:
            validate(directory, track, row["id"], reference, config)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {"status": "invalid", "diagnostic": str(error)[:1000]}
    return {"status": "integrity-checked"}


def require_rows(directory, track, rows, config=None):
    for row in rows:
        status = inspect_row(directory, track, row, config)
        if status["status"] == "invalid":
            raise ValueError("Invalid condition evidence: " + status["diagnostic"])


def review_view(directory, track, config=None):
    review = copy.deepcopy(_decode(track.get("review")))
    if review is None:
        return None
    for key in ("conditions", "additional_assessments"):
        for row in review.get(key, []):
            row["evidenceIntegrity"] = inspect_row(directory, track, row, config)
    return review
