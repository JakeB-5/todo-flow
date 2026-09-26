"""Versioned compatibility contracts; package versions do not imply data migrations."""

import json
import re
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

try:
    VERSION = version("todo-flow")
except PackageNotFoundError:
    # Copied recovery runners execute with the base interpreter.
    VERSION = "0.0.4"
CONTRACTS = json.loads(Path(__file__).with_name("release.json").read_text())
VERIFICATION_IDENTITY_VERSION = 1


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
