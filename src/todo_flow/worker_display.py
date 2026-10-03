"""Bounded, read-only worker selection and provider evidence for one attempt."""

import json
from pathlib import Path
import re


LIMIT = 65536
LABEL_LIMIT = 512
FILES = ("worker-selection.json", "native-session.json")


def _read(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("Worker evidence exceeds display limit")
        record = json.loads(raw.decode("utf-8"))
        if not isinstance(record, dict):
            raise ValueError("Invalid worker evidence")
        return "available", record
    except FileNotFoundError:
        return "missing", {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return "unreadable", {}


def _field(records, order, section, key):
    confirmed = section == "provider_confirmed"
    for filename in order:
        evidence, record = records[filename]
        if evidence != "available":
            continue
        values = record.get(section)
        if values is None:
            continue
        source = f"{filename}:{section}.{key}"
        if not isinstance(values, dict):
            return {"value": None, "status": "unreadable", "source": source}
        if key not in values:
            continue
        value = values[key]
        if value is None:
            status = "unconfirmed" if confirmed else "delegated"
        elif isinstance(value, str) and value.strip() and len(value) <= LABEL_LIMIT:
            status = "confirmed" if confirmed else "selected"
        else:
            value, status = None, "unreadable"
        return {"value": value, "status": status, "source": source}
    states = [records[filename][0] for filename in order]
    status = (
        "unreadable"
        if "unreadable" in states
        else "unconfirmed"
        if confirmed and "available" in states
        else "missing"
    )
    return {"value": None, "status": status, "source": None}


def read_worker(state, attempt):
    """Never consult current configuration, other attempts, commands or transcripts."""
    if attempt is not None and (
        not isinstance(attempt, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", attempt)
    ):
        raise ValueError("Invalid attempt ID")
    records = {
        filename: _read(Path(state) / "attempts" / attempt / filename)
        if attempt is not None
        else ("missing", {})
        for filename in FILES
    }
    result = {
        "attempt": attempt,
        "evidence": {filename: value[0] for filename, value in records.items()},
        "provider": _field(records, FILES, "selected", "provider"),
    }
    for key in ("model", "effort"):
        result[key] = {
            "selected": _field(records, FILES, "selected", key),
            # Native evidence is available while the worker is still running.
            "confirmed": _field(records, FILES[::-1], "provider_confirmed", key),
        }
    return result
