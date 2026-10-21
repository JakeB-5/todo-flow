"""One owner for repository declarations, track checkout state, and scoped paths."""

import json
from pathlib import PurePosixPath
import re

from .store import Conflict, encode, fingerprint


def multi(config):
    return bool(config.get("repos"))


def specs(config):
    if config.get("repos"):
        return [dict(row) for row in config["repos"]]
    return [
        {
            "name": "default",
            "path": config["repo"],
            "base": config["base"],
            "writable": config["writable_patterns"],
            "verify": config["verify"],
        }
    ]


def state(track, config, name):
    declarations = specs(config)
    names = [row["name"] for row in declarations]
    if not multi(config) and name is None:
        name = names[0]
    if name not in names:
        raise Conflict("Unknown repository name: " + str(name))
    if not multi(config):
        record = {key: track.get(key) for key in ("branch", "workspace", "head", "issue", "pr")}
        return _validate_state(record)
    if name == names[0]:
        record = {key: track.get(key) for key in ("branch", "workspace", "head", "issue", "pr")}
        return _validate_state(record)
    raw = track.get("repo_state")
    try:
        records = json.loads(raw) if isinstance(raw, str) and raw else raw or {}
    except (ValueError, TypeError) as error:
        raise Conflict("Invalid per-repository track state; preserve it") from error
    if not isinstance(records, dict):
        raise Conflict("Invalid per-repository track state; preserve it")
    record = records.get(name, {})
    if not isinstance(record, dict) or set(record) - {"branch", "workspace", "head", "issue", "pr"}:
        raise Conflict("Invalid per-repository track state; preserve it")
    return _validate_state(
        {key: record.get(key) for key in ("branch", "workspace", "head", "issue", "pr")}
    )


def _validate_state(record):
    for key in ("branch", "workspace"):
        value = record[key]
        if value is not None and (not isinstance(value, str) or not value or "\x00" in value):
            raise Conflict("Invalid per-repository " + key + " state; preserve it")
    head = record["head"]
    if head is not None and (
        not isinstance(head, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head)
    ):
        raise Conflict("Invalid per-repository HEAD state; preserve it")
    for key in ("issue", "pr"):
        value = record[key]
        if value is not None and (type(value) is not int or value < 1):
            raise Conflict("Invalid per-repository " + key + " state; preserve it")
    return record


def all_states(track, config):
    return {row["name"]: state(track, config, row["name"]) for row in specs(config)}


def identity_components(track):
    """Bind repository state when present, while preserving legacy single-repo keys."""
    raw = track.get("repo_state")
    if raw is None or raw == "":
        return []
    try:
        records = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as error:
        raise Conflict("Invalid per-repository track state; preserve it") from error
    if not isinstance(records, dict):
        raise Conflict("Invalid per-repository track state; preserve it")
    return [raw]


def github_repo(config, name=None):
    configured = config.get("github")
    if multi(config):
        if isinstance(configured, dict):
            if name is None:
                return None
            configured = configured.get(name)
        elif isinstance(configured, str) and name != specs(config)[0]["name"]:
            # A legacy string names the primary repository only. Never fan it
            # out to secondary repositories or silently drop the primary PR.
            return None
    return configured if isinstance(configured, str) and configured else None


def fetch_base(root, base, *, runner=None):
    """Fetch and resolve a stable remote-tracking ref, never shared FETCH_HEAD."""
    if runner is None:
        from .adapters import command

        runner = command

    runner(["git", "fetch", "origin", base], root)
    return runner(["git", "rev-parse", f"refs/remotes/origin/{base}^{{commit}}"], root)


def update(engine, task, name, *, invalidate=False, **values):
    allowed = {"branch", "workspace", "head", "issue", "pr"}
    if not set(values) <= allowed:
        raise ValueError("Unknown repository state fields")
    _validate_state(
        {key: values.get(key) for key in allowed} | {key: None for key in allowed - set(values)}
    )
    config = engine.config
    declarations = specs(config)
    names = [row["name"] for row in declarations]
    if not multi(config) and name is None:
        name = names[0]
    if name not in names:
        raise Conflict("Unknown repository name: " + str(name))
    track = engine.store.track(task["track"])
    if not multi(config) or name == names[0]:
        changes = dict(values)
    else:
        raw = track.get("repo_state")
        try:
            records = json.loads(raw) if isinstance(raw, str) and raw else raw or {}
        except (ValueError, TypeError) as error:
            raise Conflict("Invalid per-repository track state; preserve it") from error
        if not isinstance(records, dict):
            raise Conflict("Invalid per-repository track state; preserve it")
        record = records.get(name, {})
        if not isinstance(record, dict) or set(record) - allowed:
            raise Conflict("Invalid per-repository track state; preserve it")
        records[name] = {**record, **values}
        changes = {"repo_state": encode(records)}
    if invalidate:
        changes.update(verification=None, review=None, landing=None)
    engine.update(task, **changes)


def head_map(track, config):
    result = {name: record["head"] for name, record in all_states(track, config).items()}
    if any(not isinstance(head, str) or not head for head in result.values()):
        raise Conflict("Every repository must have a recorded candidate HEAD")
    return result


def identity(track, config):
    return fingerprint(head_map(track, config))


def require_clean(track, config, workspaces):
    """Require every owned checkout to remain at its registered candidate HEAD."""
    from .checkout import require_clean as require_checkout_clean

    if not multi(config):
        return require_checkout_clean(workspaces, track["head"])
    states = all_states(track, config)
    for spec in specs(config):
        name = spec["name"]
        require_checkout_clean(workspaces[name], states[name]["head"])
    return head_map(track, config)


def require_current(engine, track, workspaces, evidence=None):
    """Bind full verification to every repository's current HEAD and inputs."""
    from . import verification_evidence

    evidence = evidence if evidence is not None else track.get("verification")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError as error:
            raise Conflict("Current multi-repository verification is invalid") from error
    if not multi(engine.config):
        return verification_evidence.require_current(
            engine.config, workspaces, track["head"], evidence
        )
    declarations = specs(engine.config)
    names = [row["name"] for row in declarations]
    states = all_states(track, engine.config)
    records = evidence.get("repositories") if isinstance(evidence, dict) else None
    if (
        not isinstance(evidence, dict)
        or not isinstance(records, dict)
        or set(records) != set(names)
        or evidence.get("heads") != {name: states[name]["head"] for name in names}
    ):
        raise Conflict("Current verification of every repository is required")
    primary = records[names[0]]
    if any(
        evidence.get(key) != primary.get(key)
        for key in ("head", "tree", "key", "command", "identity", "scope", "checks", "logReference")
    ):
        raise Conflict("Aggregate verification wrapper does not match its primary repository")
    for spec in declarations:
        name = spec["name"]
        config = {
            **engine.config,
            "repo": spec["path"],
            "base": spec["base"],
            "verify": spec["verify"],
            "writable_patterns": spec["writable"],
        }
        verification_evidence.require_current(
            config,
            workspaces[name],
            states[name]["head"],
            evidence["repositories"][name],
        )
    require_clean(track, engine.config, workspaces)
    return evidence


def route_changes(config, changes):
    declarations = specs(config)
    if not multi(config):
        return {declarations[0]["name"]: changes}
    known = {row["name"] for row in declarations}
    grouped = {name: [] for name in known}
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), str):
            raise ValueError("Each multi-repository change requires a name:path string")
        name, separator, path = change["path"].partition(":")
        lexical = PurePosixPath(path) if path else None
        if (
            not separator
            or name not in known
            or not path
            or ":" in path
            or "\\" in path
            or "\x00" in path
            or (lexical is not None and (lexical.is_absolute() or lexical.as_posix() != path))
        ):
            raise Conflict("Multi-repository proposal path must name a configured repository")
        grouped[name].append({**change, "path": path})
    return {name: rows for name, rows in grouped.items() if rows}
