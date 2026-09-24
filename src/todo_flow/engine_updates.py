"""Explicit local-wheel updates for uv tool installations, with an external recovery runner.

Copied beside maintenance.py and release.py before replacement. The recovery runner
uses the base Python interpreter, not the virtual environment being replaced.
"""

import argparse
import configparser
import email.parser
import hashlib
import json
import os
import re
import shutil
import shlex
import tomllib
from importlib.metadata import distributions
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

if __package__:
    from .maintenance import home, known_states, lease, require_quiet_state, write_json
    from .release import CONTRACTS, VERSION, project_compatibility, release_number
else:
    from maintenance import home, known_states, lease, require_quiet_state, write_json

    # The copied release module cannot discover package metadata from the base Python.
    from release import CONTRACTS, VERSION, project_compatibility, release_number


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            result.stderr[-3000:] or result.stdout[-3000:] or f"Command failed: {argv[0]}"
        )
    return result.stdout.strip()


def inspect_wheel(path):
    try:
        return _inspect_wheel(path)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, configparser.Error) as error:
        raise ValueError("Invalid TODO Flow release wheel: " + str(error)) from error


def _inspect_wheel(path):
    path = Path(path).resolve()
    if not path.is_file() or path.suffix != ".whl":
        raise ValueError("Supply a local release wheel with --wheel")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected one package in the wheel")
        info = email.parser.Parser().parsestr(archive.read(metadata[0]).decode())
        if re.sub(r"[-_.]+", "-", info["Name"].lower()) != "todo-flow":
            raise ValueError("Wheel is not TODO Flow")
        release_number(info["Version"])
        contracts = json.loads(archive.read("todo_flow/release.json"))
        if contracts.get("manifest_version") != 1:
            raise ValueError("Unsupported release compatibility manifest")
        for field in ("state_formats", "config_formats", "worker_protocols", "skill_protocols"):
            if not isinstance(contracts.get(field), list) or not contracts[field]:
                raise ValueError("Incomplete release compatibility manifest")
        entrypoints = configparser.ConfigParser()
        entrypoints.read_string(
            archive.read(metadata[0].replace("METADATA", "entry_points.txt")).decode()
        )
        if set(entrypoints["console_scripts"]) != {"todo-flow", "trackrun"}:
            raise ValueError("Unexpected release entrypoints")
    return {
        "wheel": str(path),
        "version": info["Version"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "contracts": contracts,
    }


def installation():
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("uv is required for a managed engine update")
    tools = Path(run([uv, "tool", "dir"])).resolve()
    prefix = Path(sys.prefix).resolve()
    if prefix != tools / "todo-flow" or not (prefix / "uv-receipt.toml").is_file():
        raise ValueError(
            "Engine updates require a uv tool installation. Source/venv users should update their checkout or environment, then run compatibility and update-skills."
        )
    if home().is_relative_to(prefix):
        raise ValueError(
            "TODO_FLOW_HOME must be outside the engine environment so backups and recovery survive replacement"
        )
    receipt = tomllib.loads((prefix / "uv-receipt.toml").read_text())["tool"]
    requirements = receipt.get("requirements", [])
    if len(requirements) != 1 or requirements[0].get("extras") or receipt.get("options"):
        raise ValueError(
            "Custom uv tool requirements/options need their original installation workflow; automatic replacement is limited to the standard install"
        )
    if {item.get("name") for item in receipt.get("entrypoints", [])} != {"todo-flow", "trackrun"}:
        raise ValueError(
            "Custom tool entrypoints must be preserved using the original installation workflow"
        )
    for package in distributions():
        direct = package.read_text("direct_url.json")
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            raise ValueError("Editable installations require their original source update workflow")
    return {
        "uv": uv,
        "prefix": str(prefix),
        "bin": str(Path(run([uv, "tool", "dir", "--bin"])).resolve()),
        "python": str(Path(sys._base_executable).resolve()),
        "oldVersion": VERSION,
    }


def check_projects(contracts, version):
    results = []
    for state in known_states():
        if not state.exists():
            continue
        require_quiet_state(state)
        result = project_compatibility(state, contracts, version)
        if not result["compatible"]:
            raise ValueError(f"Project {state} is incompatible: " + "; ".join(result["issues"]))
        results.append(result)
    return results


def backup_entrypoints(directory, bin_dir):
    records = {}
    for name in ("todo-flow", "trackrun"):
        path = bin_dir / name
        if path.is_symlink():
            records[name] = {"link": os.readlink(path)}
        elif path.is_file():
            destination = directory / (name + ".entrypoint")
            shutil.copy2(path, destination)
            records[name] = {"file": str(destination)}
        elif path.exists():
            raise ValueError("Unexpected entrypoint type")
        else:
            records[name] = None
    return records


def restore_environment(receipt):
    directory = home() / "engine-updates" / receipt["id"]
    prefix = Path(receipt["prefix"])
    backup = directory / "environment"
    if not (backup / "uv-receipt.toml").is_file():
        raise ValueError("Complete environment backup not found")
    staged = prefix.with_name(".todo-flow-restore-" + uuid.uuid4().hex)
    shutil.copytree(backup, staged, symlinks=True)
    if prefix.exists():
        # Keep the displaced environment for inspection, on the same filesystem.
        os.replace(prefix, prefix.with_name(".todo-flow-retired-" + uuid.uuid4().hex))
    os.replace(staged, prefix)
    bin_dir = Path(receipt["bin"])
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, record in receipt["entrypoints"].items():
        path = bin_dir / name
        if record is None:
            path.unlink(missing_ok=True)
            continue
        temporary = bin_dir / (".todo-flow-link-" + uuid.uuid4().hex)
        if "link" in record:
            temporary.symlink_to(record["link"])
        else:
            shutil.copy2(record["file"], temporary)
        os.replace(temporary, path)


def installed_version(prefix):
    return run(
        [
            str(Path(prefix) / "bin/python"),
            "-c",
            "from importlib.metadata import version; print(version('todo-flow'))",
        ]
    )


def smoke(prefix, expected):
    if installed_version(prefix) != expected:
        raise RuntimeError("Installed package version does not match the release")
    for executable in ("todo-flow", "trackrun"):
        output = run([str(Path(prefix) / "bin" / executable), "--version"])
        if expected not in output:
            raise RuntimeError("Installed CLI failed its version check")
    # Import only; no runtime or remote effects during the maintenance window.
    run(
        [
            str(Path(prefix) / "bin/python"),
            "-c",
            "from todo_flow.cli import parser; from todo_flow.skill_updates import payloads; assert 'todo' in payloads(); parser()",
        ]
    )


def apply_update(spec):
    with lease(home() / "runtime.lock", exclusive=True):
        pending = home() / "engine-pending.json"
        if pending.exists():
            raise RuntimeError("Recover the interrupted engine update first")
        directory = home() / "engine-updates" / spec["id"]
        snapshot = directory / Path(spec["wheel"]).name
        shutil.copy2(spec["wheel"], snapshot)
        candidate = inspect_wheel(snapshot)
        if candidate["sha256"] != spec["sha256"]:
            raise ValueError("Wheel changed after planning")
        check_projects(candidate["contracts"], candidate["version"])
        if installed_version(spec["prefix"]) != spec["oldVersion"]:
            raise ValueError("Installed version changed after planning; retry")
        directory = home() / "engine-updates" / spec["id"]
        receipt = {**spec, "phase": "prepared"}
        shutil.copytree(spec["prefix"], directory / "environment", symlinks=True)
        receipt["entrypoints"] = backup_entrypoints(directory, Path(spec["bin"]))
        write_json(directory / "receipt.json", receipt)
        write_json(pending, {"id": spec["id"]})
        try:
            run(
                [
                    spec["uv"],
                    "tool",
                    "install",
                    "--reinstall",
                    "--python",
                    spec["python"],
                    str(snapshot),
                ]
            )
            smoke(spec["prefix"], candidate["version"])
            receipt["phase"] = "complete"
            write_json(directory / "receipt.json", receipt)
            pending.unlink()
        except BaseException:
            restore_environment(receipt)
            smoke(spec["prefix"], spec["oldVersion"])
            receipt["phase"] = "rolled-back"
            write_json(directory / "receipt.json", receipt)
            pending.unlink(missing_ok=True)
            raise
        return {
            "updated": candidate["version"],
            "previous": spec["oldVersion"],
            "backup": spec["id"],
            "next": "Run compatibility and update-skills for each project. Restart dashboards and agent sessions.",
        }


def rollback(spec):
    with lease(home() / "runtime.lock", exclusive=True):
        pending = home() / "engine-pending.json"
        recovering = spec.get("recover", False)
        if recovering:
            if not pending.exists():
                return {"unchanged": True}
            identifier = json.loads(pending.read_text())["id"]
        else:
            if pending.exists():
                raise RuntimeError("Recover the interrupted update first")
            identifier = spec["rollback"]
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid engine backup ID")
        record = home() / "engine-updates" / identifier / "receipt.json"
        receipt = json.loads(record.read_text())
        if spec.get("prefix") and spec["prefix"] != receipt["prefix"]:
            raise ValueError("Backup belongs to another installation")
        if recovering and receipt["phase"] in ("complete", "rolled-back"):
            smoke(
                receipt["prefix"],
                receipt["version"] if receipt["phase"] == "complete" else receipt["oldVersion"],
            )
            pending.unlink()
            return {"recovered": identifier, "phase": receipt["phase"]}
        if not recovering:
            if (
                receipt["phase"] != "complete"
                or installed_version(receipt["prefix"]) != receipt["version"]
            ):
                raise ValueError("Backup is not the current installed release")
        # Never downgrade an engine across a data contract it can no longer read.
        check_projects(receipt["oldContracts"], receipt["oldVersion"])
        receipt["phase"] = "rolling-back"
        write_json(record, receipt)
        write_json(pending, {"id": identifier})
        restore_environment(receipt)
        smoke(receipt["prefix"], receipt["oldVersion"])
        receipt["phase"] = "rolled-back"
        write_json(record, receipt)
        pending.unlink()
        return {"restored": receipt["oldVersion"], "backup": identifier}


def launch(wheel=None, *, dry_run=False, restore=None, recover=False):
    info = installation()
    if restore or recover:
        spec = {**info, "rollback": restore, "recover": recover}
    else:
        candidate = inspect_wheel(wheel)
        if release_number(candidate["version"]) <= release_number(VERSION):
            raise ValueError(
                "Choose a newer release; use --rollback BACKUP_ID to restore a previous installation"
            )
        with lease(home() / "runtime.lock", exclusive=True):
            if (home() / "engine-pending.json").exists():
                raise RuntimeError("Recover the interrupted engine update first")
            projects = check_projects(candidate["contracts"], candidate["version"])
        spec = {**info, **candidate, "oldContracts": CONTRACTS}
        if dry_run:
            return {**spec, "projects": projects, "dryRun": True}
    identifier = uuid.uuid4().hex
    directory = home() / "engine-updates" / identifier
    directory.mkdir(parents=True, mode=0o700)
    spec["id"] = identifier
    for name in ("engine_updates.py", "maintenance.py", "release.py", "release.json"):
        shutil.copy2(Path(__file__).with_name(name), directory / name)
    write_json(directory / "request.json", spec)
    # The independent runner stays usable even if the current CLI disappears mid-install.
    recovery_command = shlex.join(
        [info["python"], str(directory / "engine_updates.py"), "--recover"]
    )
    print("Recovery runner: " + recovery_command, file=sys.stderr, flush=True)
    result = run(
        [
            info["python"],
            str(directory / "engine_updates.py"),
            "--request",
            str(directory / "request.json"),
        ]
    )
    return json.loads(result)


if __name__ == "__main__":
    runner = Path(__file__).resolve()
    if runner.parent.parent.name == "engine-updates":
        # Recovery must still work when the caller no longer has the original environment variables.
        os.environ["TODO_FLOW_HOME"] = str(runner.parents[2])
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        if args.recover:
            result = rollback({"recover": True})
        else:
            spec = json.loads(Path(args.request).read_text())
            result = (
                rollback(spec)
                if spec.get("rollback") or spec.get("recover")
                else apply_update(spec)
            )
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
