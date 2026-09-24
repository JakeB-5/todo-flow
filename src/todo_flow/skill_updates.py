"""Manifest-based skill installation, three-way updates and recoverable backups."""

import base64
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from .maintenance import (
    runtime_guard,
    home,
    lease,
    project_key,
    project_lock,
    require_quiet_state,
    write_json,
)
from .release import VERSION, CONTRACTS, project_compatibility

MANIFEST = ".todo-flow-install.json"


def bundle():
    source = Path(__file__).with_name("skills")
    return source if source.exists() else Path(__file__).resolve().parents[2] / "skills"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
        raise ValueError("Invalid managed skill path")
    target = root / path
    for part in [target, *target.parents]:
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError(f"Managed skill path is a symlink: {target}")
    return target


def payloads(source=None):
    result = {}
    for folder in sorted(Path(source or bundle()).iterdir()):
        if not folder.is_dir() or not (folder / "SKILL.md").is_file():
            continue
        if not re.fullmatch(r"[a-z0-9-]+", folder.name):
            raise ValueError("Invalid bundled skill name")
        result[folder.name] = {
            str(file.relative_to(folder)): file.read_bytes()
            for file in sorted(folder.rglob("*"))
            if file.is_file() and str(file.relative_to(folder)) not in (MANIFEST, "project.json")
        }
    if not result:
        raise ValueError("No packaged skills found")
    return result


def manifest(files, version=VERSION):
    return {
        "format": 1,
        "engine_version": version,
        "skill_protocol": 1,
        "files": {name: digest(data) for name, data in files.items()},
    }


def signature(folder):
    if not folder.exists() and not folder.is_symlink():
        return None
    if folder.is_symlink():
        return "symlink:" + os.readlink(folder)
    result = []
    for p in sorted(folder.rglob("*")):
        value = (
            ("link:" + os.readlink(p))
            if p.is_symlink()
            else digest(p.read_bytes())
            if p.is_file()
            else "directory"
        )
        result.append((str(p.relative_to(folder)), value))
    return digest(json.dumps(result).encode())


def pending_path(target):
    return home() / "skill-targets" / project_key(target) / "pending.json"


def plan(target, source=None, adopt=False, version=VERSION):
    target = Path(target).resolve()
    if target == Path(source or bundle()).resolve():
        raise ValueError("Target the installed project skills, not the bundled source directory")
    updates, conflicts, preserved, actions = {}, [], [], []
    versions = {}
    bundled = payloads(source)
    retired = {p.parent.name for p in target.glob("*/" + MANIFEST)} - set(bundled)
    for name in sorted(set(bundled) | retired):
        files = bundled.get(name, {})
        folder = target / name
        changes = {}
        record = safe_path(folder, MANIFEST)
        if folder.exists():
            if not record.exists():
                if not adopt:
                    conflicts.append(
                        f"{name}: no installation manifest; use --adopt only with the matching original bundle"
                    )
                    continue
                previous = {path: digest(data) for path, data in files.items()}
                for path, expected in previous.items():
                    local = safe_path(folder, path)
                    if not local.is_file() or digest(local.read_bytes()) != expected:
                        conflicts.append(
                            f"{name}/{path}: legacy content differs; cannot infer its baseline"
                        )
            else:
                installed = json.loads(record.read_text())
                if (
                    installed.get("format") != 1
                    or installed.get("skill_protocol") not in CONTRACTS["skill_protocols"]
                ):
                    raise ValueError(f"Unsupported skill installation format/protocol: {name}")
                versions[name] = installed.get("engine_version")
                previous = installed.get("files")
                if not isinstance(previous, dict) or any(
                    not isinstance(path, str)
                    or path in (MANIFEST, "project.json")
                    or not isinstance(value, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", value)
                    for path, value in previous.items()
                ):
                    raise ValueError(f"Invalid skill file manifest: {name}")
            for path in sorted(set(previous) | set(files)):
                local = safe_path(folder, path)
                current = digest(local.read_bytes()) if local.is_file() else None
                old = previous.get(path)
                new = digest(files[path]) if path in files else None
                if current == new:
                    continue
                if current == old:
                    changes[path] = files.get(path)
                    actions.append(
                        {
                            "skill": name,
                            "path": path,
                            "action": "remove" if new is None else "replace",
                        }
                    )
                elif new == old:
                    preserved.append(f"{name}/{path}")
                else:
                    conflicts.append(
                        f"{name}/{path}: both local content and the bundled file changed"
                    )
        else:
            changes = dict(files)
            actions.append({"skill": name, "action": "install"})
        changes[MANIFEST] = (json.dumps(manifest(files, version), indent=2) + "\n").encode()
        updates[name] = changes
    return {
        "target": str(target),
        "installedVersions": versions,
        "retired": sorted(retired),
        "version": version,
        "actions": actions,
        "preserved": preserved,
        "conflicts": conflicts,
    }, updates


def restore_snapshot(receipt):
    target = Path(receipt["target"])
    backup = home() / "skill-updates" / receipt["id"]
    for name, existed in receipt["before"].items():
        folder = target / name
        if folder.is_symlink():
            raise ValueError("Refusing to replace a symlinked skill directory")
        staged = backup / (name + ".restore-" + uuid.uuid4().hex)
        if existed is not None:
            shutil.copytree(backup / "before" / name, staged, symlinks=True)
        if folder.exists():
            os.replace(folder, backup / (name + ".retired-" + uuid.uuid4().hex))
        if existed is not None:
            os.replace(staged, folder)


def transact(target, state, updates, report, language, *, install=False):
    identifier = uuid.uuid4().hex
    directory = home() / "skill-updates" / identifier
    if any(directory.is_relative_to(target / name) for name in updates):
        raise ValueError("Update backups must be outside the managed skill directories")
    directory.mkdir(parents=True, mode=0o700)
    receipt = {
        "id": identifier,
        "target": str(target),
        "state": str(state),
        "version": report["version"],
        "phase": "prepared",
        "before": {name: signature(target / name) for name in updates},
    }
    for name, before in receipt["before"].items():
        if before is not None:
            shutil.copytree(target / name, directory / "before" / name, symlinks=True)
    write_json(directory / "receipt.json", receipt)
    write_json(pending_path(target), {"id": identifier})
    try:
        for name, files in updates.items():
            folder = target / name
            folder.mkdir(parents=True, exist_ok=True)
            for relative, data in files.items():
                path = safe_path(folder, relative)
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    # Whole-file replacement; the before-image is durable before any write.
                    from .file_store import atomic

                    atomic(path, {"base64": base64.b64encode(data).decode()})
            if install or not (folder / "project.json").exists():
                write_json(folder / "project.json", {"state": str(state), "language": language})
        receipt["after"] = {name: signature(target / name) for name in updates}
        receipt["phase"] = "complete"
        write_json(directory / "receipt.json", receipt)
        pending_path(target).unlink()
    except BaseException:
        restore_snapshot(receipt)
        receipt["phase"] = "rolled-back"
        write_json(directory / "receipt.json", receipt)
        pending_path(target).unlink(missing_ok=True)
        raise
    return {
        **report,
        "backup": identifier,
        "language": language,
        "state": str(state),
        "installed": str(target),
    }


def update(
    target,
    state,
    language="en",
    *,
    dry_run=False,
    adopt=False,
    install=False,
    source=None,
    version=VERSION,
):
    target, state = Path(target).resolve(), Path(state).resolve()
    with (
        runtime_guard(),
        lease(project_lock(state), exclusive=True),
        lease(pending_path(target).with_name("runtime.lock"), exclusive=True),
    ):
        if pending_path(target).exists():
            raise RuntimeError(
                "Interrupted skill update; run update-skills --target PATH --recover"
            )
        compatibility = project_compatibility(state)
        if not compatibility["compatible"]:
            raise ValueError("; ".join(compatibility["issues"]))
        require_quiet_state(state)
        if install and any((target / name).exists() for name in payloads(source)):
            raise ValueError(
                "Skill already exists; use update-skills to review and apply a safe update"
            )
        report, updates = plan(target, source, adopt, version)
        if dry_run:
            return {**report, "dryRun": True}
        if report["conflicts"]:
            raise ValueError(
                "Skill update conflicts; nothing changed: " + "; ".join(report["conflicts"])
            )
        # A no-op needs no backup and leaves user-owned context untouched.
        changes = any(
            data
            != (
                safe_path(target / name, path).read_bytes()
                if safe_path(target / name, path).is_file()
                else None
            )
            for name, files in updates.items()
            for path, data in files.items()
        )
        changes = changes or any(not (target / name / "project.json").exists() for name in updates)
        if not changes:
            return {**report, "unchanged": True}
        return transact(target, state, updates, report, language, install=install)


def restore(target, state, identifier=None):
    target, state = Path(target).resolve(), Path(state).resolve()
    with (
        runtime_guard(),
        lease(project_lock(state), exclusive=True),
        lease(pending_path(target).with_name("runtime.lock"), exclusive=True),
    ):
        require_quiet_state(state)
        pending = pending_path(target)
        recovering = identifier is None
        if recovering:
            if not pending.exists():
                return {"unchanged": True, "target": str(target)}
            identifier = json.loads(pending.read_text())["id"]
        elif pending.exists():
            raise RuntimeError(
                "Recover the pending skill update before rolling back another update"
            )
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid skill backup ID")
        record = home() / "skill-updates" / identifier / "receipt.json"
        receipt = json.loads(record.read_text())
        if receipt["target"] != str(target) or receipt["state"] != str(state):
            raise ValueError("Backup belongs to a different target or project")
        if recovering and receipt["phase"] in ("complete", "rolled-back"):
            pending.unlink()
            return {"recovered": identifier, "phase": receipt["phase"]}
        if not recovering:
            if receipt["phase"] != "complete":
                raise ValueError("Only a completed update can be rolled back")
            current = {name: signature(target / name) for name in receipt["after"]}
            if current != receipt["after"]:
                raise ValueError(
                    "Skills changed after the update; preserve/reconcile those edits before rollback"
                )
            write_json(pending, {"id": identifier})
        receipt["phase"] = "rolling-back"
        write_json(record, receipt)
        restore_snapshot(receipt)
        receipt["phase"] = "rolled-back"
        write_json(record, receipt)
        pending.unlink()
        return {"restored": identifier, "target": str(target)}
