"""Versioned compatibility contracts; package versions do not imply data migrations."""

import json
import re
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

try:
    VERSION = version("todo-flow")
except PackageNotFoundError:
    # Copied recovery runners execute with the base interpreter.
    VERSION = "0.1.2"
CONTRACTS = json.loads(Path(__file__).with_name("release.json").read_text())
VERIFICATION_IDENTITY_VERSION = 1
CODEX_SANDBOX_VALUES = ("read-only", "workspace-write")


def validate_codex_sandbox(value, setting="codex_sandbox"):
    if type(value) is not str or value not in CODEX_SANDBOX_VALUES:
        raise ValueError(f"{setting} must be one of: {', '.join(CODEX_SANDBOX_VALUES)}")
    return value


def validate_verify_preflight(config):
    """Return ordered, detached argv arrays; absence preserves existing behavior."""
    return _validate_verify_checks(config, "verify_preflight")


def validate_verify_related(config):
    """Explicit intermediate checks; an empty list keeps full verification."""
    return _validate_verify_checks(config, "verify_related")


def _validate_verify_checks(config, field):
    checks = config.get(field, [])
    if not isinstance(checks, list):
        raise ValueError(f"{field} must be a JSON array of argv arrays")
    for argv in checks:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
            or not argv[0]
        ):
            raise ValueError(f"{field} commands must be nonempty argv arrays")
    return [list(argv) for argv in checks]


def validate_verify_identity(config):
    """Validate declarations without observing files or environment values.

    Keep this contract in the standalone release module: copied update recovery
    runners must validate configuration without importing the installed engine.
    Absence supports legacy configuration, not identity-free success evidence.
    """
    if "verify_identity" not in config:
        return {
            "version": VERIFICATION_IDENTITY_VERSION,
            "files": [],
            "environment": [],
            "nonce": "",
        }
    declaration = config["verify_identity"]
    if not isinstance(declaration, dict):
        raise ValueError("verify_identity must be an object")
    if set(declaration) - {"version", "files", "environment", "nonce"}:
        raise ValueError("Unknown verify_identity fields")
    if (
        type(declaration.get("version")) is not int
        or declaration["version"] != VERIFICATION_IDENTITY_VERSION
    ):
        raise ValueError("Unsupported verification identity configuration version")
    result = {"version": VERIFICATION_IDENTITY_VERSION}
    for field in ("files", "environment"):
        values = declaration.get(field, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or "\0" in value for value in values
        ):
            raise ValueError(f"verify_identity.{field} must contain nonempty strings")
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate verify_identity.{field} entries")
        result[field] = sorted(values)
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in result["environment"]):
        raise ValueError("Invalid verification environment variable name")
    nonce = declaration.get("nonce", "")
    if not isinstance(nonce, str):
        raise ValueError("verify_identity.nonce must be a string")
    result["nonce"] = nonce
    return result


def validate_repositories(config):
    """Validate the optional multi-repository contract without opening repositories."""
    if "repos" not in config:
        return None
    repos = config["repos"]
    if not isinstance(repos, list) or len(repos) < 2:
        raise ValueError("repos must contain at least two repository objects")
    required = {"name", "path", "base", "writable", "verify"}
    names = set()
    paths = set()
    for index, repo in enumerate(repos):
        if not isinstance(repo, dict) or set(repo) != required:
            raise ValueError(
                f"repos[{index}] must contain exactly name, path, base, writable, verify"
            )
        name = repo["name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name)
            or name.casefold() in names
        ):
            raise ValueError(f"repos[{index}].name must be unique and filesystem-safe")
        names.add(name.casefold())
        path = repo["path"]
        if not isinstance(path, str) or not path or "\0" in path or not Path(path).is_absolute():
            raise ValueError(f"repos[{index}].path must be an absolute path")
        normalized = str(Path(path))
        if normalized in paths:
            raise ValueError("repos paths must be unique")
        paths.add(normalized)
        base = repo["base"]
        if (
            not isinstance(base, str)
            or not base
            or base.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", base)
            or ".." in base
            or "//" in base
            or any(part in ("", ".") or part.endswith(".lock") for part in base.split("/"))
        ):
            raise ValueError(f"repos[{index}].base must be a valid branch name")
        writable = repo["writable"]
        if (
            not isinstance(writable, list)
            or not writable
            or any(
                not isinstance(pattern, str) or not pattern or "\0" in pattern
                for pattern in writable
            )
            or len(set(writable)) != len(writable)
        ):
            raise ValueError(f"repos[{index}].writable must contain unique nonempty patterns")
        verify = repo["verify"]
        if (
            not isinstance(verify, list)
            or not verify
            or any(not isinstance(arg, str) or "\0" in arg for arg in verify)
            or not verify[0]
        ):
            raise ValueError(f"repos[{index}].verify must be a nonempty argv array")
    primary = repos[0]
    if "repo" in config and Path(config["repo"]).resolve() != Path(primary["path"]).resolve():
        raise ValueError("repo must match repos[0].path")
    for field, repo_field in (
        ("base", "base"),
        ("verify", "verify"),
        ("writable_patterns", "writable"),
    ):
        if field in config and config[field] != primary[repo_field]:
            raise ValueError(f"{field} must match repos[0].{repo_field}")
    return repos


def validate_github(config):
    github = config.get("github")
    if github is None:
        return
    if isinstance(github, str):
        values = [github]
    elif isinstance(github, dict) and config.get("repos"):
        names = {row["name"] for row in config["repos"]}
        if not github or not set(github) <= names:
            raise ValueError("github object keys must name configured repositories")
        values = list(github.values())
    else:
        raise ValueError("github must be owner/repository or a repository-name map")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", value)
        for value in values
    ):
        raise ValueError("github must contain owner/repository names")


def release_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Updates currently require a stable major.minor.patch version")
    return tuple(map(int, value.split(".")))


def check_catalog(catalog, contracts=CONTRACTS, engine_version=VERSION):
    if not isinstance(catalog, dict):
        raise ValueError("Invalid state catalog")
    if (
        type(catalog.get("format")) is not int
        or catalog["format"] not in contracts["state_formats"]
    ):
        raise ValueError(
            f"Unsupported state format: {catalog.get('format')}; preserve the state and use a compatible engine"
        )
    minimum = catalog.get("min_engine_version", "0.0.1")
    if release_number(engine_version) < release_number(minimum):
        raise ValueError(f"State requires TODO Flow >= {minimum}")


def check_config(config, contracts=CONTRACTS, engine_version=VERSION):
    if not isinstance(config, dict):
        raise ValueError("Invalid project configuration")
    for key, accepted in (
        ("schema_version", "config_formats"),
        ("worker_protocol", "worker_protocols"),
    ):
        if type(config.get(key, 1)) is not int or config.get(key, 1) not in contracts[accepted]:
            raise ValueError(f"Unsupported project {key}: {config.get(key)}")
    if release_number(engine_version) < release_number(config.get("min_engine_version", "0.0.1")):
        raise ValueError("Project requires a newer engine")
    validate_verify_identity(config)
    validate_verify_preflight(config)
    validate_verify_related(config)
    if "codex_sandbox" in config:
        validate_codex_sandbox(config["codex_sandbox"])
    repos = validate_repositories(config)
    validate_github(config)
    worker = config.get("worker")
    if (
        repos
        and worker is not None
        and (not isinstance(worker, dict) or worker.get("type") != "command")
    ):
        raise ValueError("Multi-repository projects require a command worker adapter")
    worktree_root = config.get("worktree_root")
    if worktree_root is not None and (
        not repos
        or not isinstance(worktree_root, str)
        or not worktree_root
        or "\0" in worktree_root
        or not Path(worktree_root).is_absolute()
    ):
        raise ValueError("worktree_root requires multi-repository projects and an absolute path")
    if "verify_concurrency" in config:
        limit = config["verify_concurrency"]
        if type(limit) is not int or limit < 1:
            raise ValueError("verify_concurrency must be a positive integer")
    timeout = config.get("worker_timeout")
    if timeout is not None and (type(timeout) is not int or timeout < 0):
        raise ValueError("worker_timeout must be null (unlimited) or nonnegative seconds")


def project_compatibility(state, contracts=CONTRACTS, engine_version=VERSION):
    state = Path(state).resolve()
    result = {"state": str(state), "engine": engine_version, "compatible": True, "issues": []}
    catalog = state / ".catalog.json"
    if not catalog.exists():
        result.update(initialized=False)
        if (state / "state.sqlite").exists():
            result["issues"].append("Legacy SQLite state requires migrate-files")
    else:
        result["initialized"] = True
        try:
            data = json.loads(catalog.read_text())
            check_catalog(data, contracts, engine_version)
            result["stateFormat"] = data["format"]
            config_path = state / "config/1.json"
            if config_path.exists():
                config = json.loads(config_path.read_text())["body"]
                check_config(config, contracts, engine_version)
                result["configFormat"] = config.get("schema_version", 1)
                result["workerProtocol"] = config.get("worker_protocol", 1)
        except (ValueError, KeyError, TypeError) as error:
            result["issues"].append(str(error))
    if (state / ".pending.json").exists():
        result["issues"].append(
            "Pending state transaction: run status with the current compatible engine first"
        )
    result["compatible"] = not result["issues"]
    return result
