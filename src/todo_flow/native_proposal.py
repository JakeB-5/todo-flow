"""Strict proposal checks for a future native transcript adapter.

This is a host-internal contract, NOT an Orca response schema. No native route
calls this module yet. The transport must first prove the exact final assistant
message and its complete contents using a documented Orca contract. Parsing a
complete JSON object cannot distinguish an intermediate message from a final one.

Bindings must come from fenced host records and independently checked transport
identity, never from model output. The caller must still recheck the active claim
and HEAD under its effect lock, and preserve the existing process exit barrier.
These helpers perform no launches, writes, retries, or effect authorization.
"""

from dataclasses import dataclass, fields
import json
import re

from .worker import codex_schema, validate


@dataclass(frozen=True)
class NativeProposalBinding:
    """Host-owned attribution; external field mapping remains unimplemented."""

    attempt: str
    task: str
    generation: int
    head: str
    kind: str
    host: str
    worktree: str
    dispatch: str
    session: str
    turn: str

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "generation":
                if type(value) is not int or value < 1:
                    raise ValueError("Native proposal requires a positive claim generation")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError("Missing native proposal binding: " + field.name)
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.head) is None:
            raise ValueError("Native proposal requires an exact Git object ID")
        if self.kind not in {"assess", "work", "review", "triage", "watch"}:
            raise ValueError("Unsupported native worker role")


def check_binding(launch, current, implementation_sessions=frozenset()):
    """Compare host snapshots; this does not authenticate a transport receipt."""
    if type(launch) is not NativeProposalBinding or type(current) is not NativeProposalBinding:
        raise ValueError("Native proposal requires host binding records")
    if launch != current:
        raise ValueError("Native proposal binding changed; reconcile without resending")
    if not isinstance(implementation_sessions, frozenset) or any(
        not isinstance(identity, tuple)
        or len(identity) != 2
        or any(not isinstance(part, str) or not part.strip() for part in identity)
        for identity in implementation_sessions
    ):
        raise ValueError("Invalid implementation session provenance")
    if current.kind == "review":
        if not implementation_sessions:
            raise ValueError("Review requires implementation session provenance")
        if (current.host, current.session) in implementation_sessions:
            raise ValueError("Review must use a fresh independent session")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-JSON numeric constant: " + value)


def _check_schema(value, schema, path="$"):
    """Validate the subset used by codex_schema; reject unsupported keywords."""
    supported = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "anyOf",
        "enum",
    }
    if set(schema) - supported:
        raise ValueError("Unsupported proposal schema keyword at " + path)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _check_schema(value, option, path)
            except ValueError:
                continue
            break
        else:
            raise ValueError("Proposal does not match any schema alternative at " + path)
    if "type" in schema:
        expected = {
            "object": dict,
            "array": list,
            "string": str,
            "boolean": bool,
            "null": type(None),
        }.get(schema["type"])
        if expected is None or type(value) is not expected:
            raise ValueError("Invalid proposal type at " + path)
    if "enum" in schema and not any(
        type(value) is type(choice) and value == choice for choice in schema["enum"]
    ):
        raise ValueError("Invalid proposal enum at " + path)
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError("Missing proposal properties at " + path)
        if schema.get("additionalProperties") is not False:
            raise ValueError("Proposal object schema must reject additional properties")
        if set(value) - set(properties):
            raise ValueError("Unknown proposal properties at " + path)
        for key, item in value.items():
            _check_schema(item, properties[key], path + "." + key)
    elif schema.get("type") == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", len(value)
        ):
            raise ValueError("Invalid proposal array length at " + path)
        for index, item in enumerate(value):
            _check_schema(item, schema["items"], f"{path}[{index}]")
    elif schema.get("type") == "string":
        value.encode("utf-8", errors="strict")


def decode_native_proposal(text, *, launch, current, implementation_sessions=frozenset()):
    """Check a whole final-message body AFTER transport finality verification.

    Never pass terminal text, a tail preview, or an arbitrary JSON-looking
    assistant message here. Transport completeness and process exit are separate
    obligations which this decoder cannot establish. No substring extraction,
    Markdown unwrapping, or last-valid-JSON recovery is attempted.
    """
    check_binding(launch, current, implementation_sessions)
    if not isinstance(text, str):
        raise ValueError("Native proposal must be UTF-8 text")
    try:
        text.encode("utf-8", errors="strict")
        result = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        _check_schema(result, codex_schema())
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid native proposal encoding or nesting") from error
    result = {key: value for key, value in result.items() if value is not None}
    if result.get("question") and any(
        result.get(key)
        for key in (
            "changes",
            "verify",
            "publish",
            "next",
            "watches",
            "findings",
            "triage",
            "triage_search",
        )
    ):
        raise ValueError("A decision wait cannot request effects")
    return validate(result, current.kind)
