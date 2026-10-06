"""Read-only version inventory and update-plan explanation.

Diagnostics never acquire runtime leases, create TODO_FLOW_HOME, write project state
or skills, execute discovered executables, or contact hosts other than loopback.
"""

import email.parser
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from .engine_updates import inspect_wheel
from .maintenance import project_key
from .release import VERSION, project_compatibility, release_number
from .skill_updates import MANIFEST, plan as skill_plan

LOOPBACK = ("127.0.0.1", "localhost")
TIMEOUT = 2
SKILLS = "todo_flow/skills/"
REFUSED = "Only http://127.0.0.1 or localhost dashboards are queried"
SOURCE_UPDATE = "Update the source checkout or environment using its usual workflow"
RESTART = "Apply the skill update without --dry-run; restart dashboards and agent sessions"
NOT_UV = (
    "This engine is a {kind} installation, not a uv tool; upgrade --wheel cannot replace it."
    " Update it with its usual workflow, or rerun this check with the uv tool CLI."
)
FIRST = {
    "applicable": "No blocker observed; still run the guarded commands below",
    "blocked": "Stop dashboards/drivers or let work finish; diagnostics never stop processes",
    "conflict": "Reconcile the listed local skill edits before updating skills",
    "unknown": "Resolve unknown items first; version numbers alone do not prove compatibility",
}


def runtime_home():
    """Resolve TODO_FLOW_HOME as maintenance.home() does, without creating it."""
    default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "todo-flow"
    return Path(os.environ.get("TODO_FLOW_HOME", default)).expanduser().resolve()


def entry(source, path, version=None, status="unknown", **details):
    path = None if path is None else str(path)
    return {"source": source, "path": path, "version": version, "status": status, **details}


def installation_kind():
    prefix = Path(sys.prefix).resolve()
    package = Path(__file__).resolve().parent
    if (prefix / "uv-receipt.toml").is_file():
        return "uv-tool"
    checkout = package.parents[1] / "pyproject.toml"
    if not package.is_relative_to(prefix) and checkout.is_file():
        return "source"
    return "venv" if sys.prefix != sys.base_prefix else "system"


def process_entry():
    return entry(
        "process",
        Path(__file__).resolve().parent,
        VERSION,
        "observed",
        prefix=str(Path(sys.prefix).resolve()),
        executable=sys.executable,
        installation=installation_kind(),
    )


def shebang_interpreter(path):
    with open(path, "rb") as file:
        head = file.read(4096).decode("utf-8", "replace")
    words = head[2:].partition("\n")[0].split() if head.startswith("#!") else []
    if not words or Path(words[0]).name == "env":
        return None
    if Path(words[0]).name == "sh":
        # Long interpreter paths use a /bin/sh trampoline instead of a direct header.
        match = re.search(r"'''exec' +\"?([^\"\s]+)", head)
        return match[1] if match else None
    return words[0]


def installed_metadata(environment):
    found = []
    for path in sorted(environment.glob("lib/python*/site-packages/*.dist-info/METADATA")):
        if not re.match(r"todo[-_.]flow-", path.parent.name, re.IGNORECASE):
            continue
        info = email.parser.Parser().parsestr(path.read_text(errors="replace"))
        if re.sub(r"[-_.]+", "-", str(info["Name"]).lower()) == "todo-flow":
            found.append((path, info["Version"]))
    return found


def executable_entry(name):
    """Read the script's interpreter environment metadata; never execute the script."""
    found = shutil.which(name)
    source = "path:" + name
    if not found:
        return entry(source, None, reason="Not found on PATH")
    try:
        interpreter = shebang_interpreter(found)
        if interpreter is None:
            return entry(source, found, reason="No readable interpreter header; not executed")
        environment = Path(interpreter).absolute().parent.parent
        installed = installed_metadata(environment)
        uv_tool = (environment / "uv-receipt.toml").is_file()
    except OSError as error:
        return entry(source, found, reason="Unreadable: " + str(error))
    details = {"interpreter": interpreter, "environment": str(environment), "uvTool": uv_tool}
    if len(installed) != 1 or not installed[0][1]:
        return entry(source, found, reason="No single todo-flow distribution found", **details)
    metadata, version = installed[0]
    details["metadata"] = str(metadata)
    return entry(source, found, version, "observed", **details)


def skill_entries(target):
    target = Path(target).resolve()
    if not target.is_dir():
        return [entry("skills", target, reason="Skill target directory not found")]
    entries = []
    for folder in sorted(target.iterdir()):
        source = "skill:" + folder.name
        if not (folder / MANIFEST).is_file():
            if (folder / "SKILL.md").is_file():
                entries.append(entry(source, folder, reason="No installation manifest"))
            continue
        try:
            record = json.loads((folder / MANIFEST).read_text())
        except (OSError, ValueError) as error:
            entries.append(entry(source, folder, reason="Unreadable manifest: " + str(error)))
            continue
        record = record if isinstance(record, dict) else {}
        version = record.get("engine_version")
        status = "observed" if isinstance(version, str) else "unknown"
        version = version if status == "observed" else None
        protocol = record.get("skill_protocol")
        entries.append(entry(source, folder, version, status, skillProtocol=protocol))
    return entries


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def dashboard_entry(url):
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return entry("dashboard", url, reason="Invalid dashboard URL")
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK or parsed.username:
        return entry("dashboard", url, reason=REFUSED)
    address = parsed.hostname + (f":{port}" if port else "")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(f"http://{address}/api/version", timeout=TIMEOUT) as response:
            body = json.loads(response.read(65536))
    except urllib.error.HTTPError as error:
        error.close()
        reason = f"HTTP {error.code} from the dashboard"
        if error.code == 404:
            reason = "Version endpoint unsupported; possibly an older dashboard"
        return entry("dashboard", url, reason=reason, responding=True)
    except OSError as error:
        return entry("dashboard", url, status="unreachable", reason=str(error))
    except ValueError:
        return entry("dashboard", url, reason="Invalid version response", responding=True)
    version = body.get("version") if isinstance(body, dict) else None
    if not isinstance(version, str):
        return entry("dashboard", url, reason="Invalid version response", responding=True)
    contracts = body.get("contracts")
    return entry("dashboard", url, version, "observed", contracts=contracts, responding=True)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def process_alive(pid):
    try:
        os.kill(pid, 0)  # Signal 0 only checks existence.
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def state_activity(state):
    """Collect what maintenance.require_quiet_state would reject, without raising."""
    reasons = []
    for file in sorted((state / "tasks").glob("*.json")):
        if read_json(file).get("status") == "running":
            reasons.append(f"{state}: unresolved running task {file.stem}")
    for file in sorted((state / "attempt-records").glob("*.json")):
        attempt = read_json(file)
        pid = attempt.get("pid")
        if attempt.get("status") == "running" and type(pid) is int and pid > 0:
            if process_alive(pid):
                reasons.append(f"{state}: recorded worker process {pid} may be active")
    return reasons


def known_states(home):
    states = []
    for file in sorted((home / "projects").glob("*/project.json")):
        state = read_json(file).get("state")
        if isinstance(state, str):
            states.append(Path(state).resolve())
    return states


def runtime_markers(home, target):
    reasons = []
    if (home / "engine-pending.json").exists():
        reasons.append("Interrupted engine update; run todo-flow upgrade --recover first")
    if target:
        pending = home / "skill-targets" / project_key(target) / "pending.json"
        if pending.exists():
            reasons.append("Interrupted skill update; run update-skills --recover first")
    return reasons


def wheel_skill_plan(wheel, target, version):
    """Plan against the wheel's bundled skills in a temporary copy; the target is only read."""
    with tempfile.TemporaryDirectory(prefix="todo-flow-diagnose-") as directory:
        source = Path(directory) / "skills"
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if not name.startswith(SKILLS) or name.endswith("/"):
                    continue
                relative = PurePosixPath(name.removeprefix(SKILLS))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe bundled skill path in the wheel")
                path = source.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
        if not source.is_dir():
            raise ValueError("The wheel contains no bundled skills")
        return skill_plan(target, source=source, version=version)[0]


def step(text, argv=None):
    return {"step": text, "command": shlex.join(argv) if argv else None}


def steps(result, state, target, inventory):
    base = ["todo-flow", "--state", str(state)]
    selected = ["--target", str(Path(target).resolve())] if target else []
    skills = selected or ["--target", "SKILLS_DIR"]
    upgrade = ["todo-flow", "upgrade", "--wheel", result["wheel"]]
    responding = [item["path"] for item in inventory if item.get("responding")]
    stop = "Stop drivers and dashboards normally"
    if responding:
        stop += " (responding: " + ", ".join(responding) + ")"
    plan = [step(FIRST[result["verdict"]]), step(stop)]
    if result["installation"] == "uv-tool":
        plan.append(step("Plan the engine update", upgrade + ["--dry-run"]))
        plan.append(step("Apply it after reviewing the plan", upgrade))
    else:
        plan.append(step(SOURCE_UPDATE))
    plan.append(step("Check project compatibility", base + ["compatibility", *selected]))
    review = base + ["update-skills", *skills, "--dry-run"]
    plan.append(step("Review the skill update plan", review))
    plan.append(step(RESTART))
    return plan


def update_plan(wheel, state, target, inventory, home):
    """Explain a supplied release using existing contracts, never version numbers alone."""
    result = {
        "wheel": str(Path(wheel).resolve()),
        "verdict": "unknown",
        "candidate": None,
        "current": VERSION,
        "installation": inventory[0]["installation"],
        "blocked": [],
        "conflicts": [],
        "unknown": [],
        "notes": [],
        "projects": [],
        "skills": None,
    }
    try:
        candidate = inspect_wheel(wheel)
    except (ValueError, OSError) as error:
        result["unknown"].append("Release compatibility manifest is unclear: " + str(error))
        result["steps"] = steps(result, state, target, inventory)
        return result
    result.update(candidate=candidate["version"], sha256=candidate["sha256"])
    for item in inventory:
        if item.get("responding"):
            url = item["path"]
            result["blocked"].append(f"Dashboard {url} responds; stop it before updating")
    result["blocked"] += runtime_markers(home, target)
    if result["installation"] != "uv-tool":
        result["unknown"].append(NOT_UV.format(kind=result["installation"]))
    else:
        try:
            newer = release_number(candidate["version"]) > release_number(VERSION)
        except ValueError as error:
            result["unknown"].append(str(error))
            newer = True
        if not newer:
            message = f"Candidate {candidate['version']} is not newer than {VERSION}"
            result["blocked"].append(message + "; upgrade refuses it")
    for item in dict.fromkeys([state, *known_states(home)]):
        if not item.exists():
            continue
        try:
            report = project_compatibility(item, candidate["contracts"], candidate["version"])
        except OSError as error:
            result["unknown"].append(f"{item}: unreadable project state: {error}")
            continue
        result["projects"].append(report)
        result["blocked"] += [f"{item}: {issue}" for issue in report["issues"]]
        result["blocked"] += state_activity(item)
    if target:
        try:
            report = wheel_skill_plan(candidate["wheel"], target, candidate["version"])
        except (ValueError, OSError, zipfile.BadZipFile) as error:
            result["unknown"].append("Skill update plan unavailable: " + str(error))
        else:
            fields = ("actions", "preserved", "conflicts")
            result["skills"] = {key: report[key] for key in fields}
            result["conflicts"] = report["conflicts"]
    else:
        result["notes"].append("No --target: installed skill conflicts were not checked")
    if result["conflicts"]:
        result["verdict"] = "conflict"
    elif result["blocked"]:
        result["verdict"] = "blocked"
    elif not result["unknown"]:
        result["verdict"] = "applicable"
    result["steps"] = steps(result, state, target, inventory)
    return result


def diagnose(state=None, target=None, wheel=None, dashboards=()):
    home = runtime_home()
    state = Path(state or Path.cwd() / "todo").resolve()
    inventory = [process_entry(), executable_entry("todo-flow"), executable_entry("trackrun")]
    if target:
        inventory += skill_entries(target)
    inventory += [dashboard_entry(url) for url in dashboards]
    observed = {}
    for item in inventory:
        if item["status"] == "observed":
            observed.setdefault(item["version"], []).append(item["source"])
    result = {
        "readOnly": True,
        "home": {"path": str(home), "exists": home.is_dir()},
        "state": str(state),
        "inventory": inventory,
        "observedVersions": observed,
        "plan": None,
    }
    if wheel:
        result["plan"] = update_plan(wheel, state, target, inventory, home)
    return result


def render(result):
    home = result["home"]
    presence = "present" if home["exists"] else "absent"
    lines = [
        "TODO Flow version diagnostics (read-only)",
        f"Runtime home: {home['path']} ({presence})",
        f"State: {result['state']}",
        "",
        "Versions by source:",
    ]
    for item in result["inventory"]:
        version = item["version"] or "-"
        path = item["path"] or "-"
        line = f"  {item['source']:<20} {version:<10} {item['status']:<11} {path}"
        detail = item.get("installation") or item.get("reason")
        lines.append(line + (f"  ({detail})" if detail else ""))
    observed = result["observedVersions"]
    if len(observed) > 1:
        lines.append("Different versions observed:")
        for version, sources in observed.items():
            lines.append(f"  {version}: {', '.join(sources)}")
    plan = result["plan"]
    lines.append("")
    if plan is None:
        lines.append("Update plan: supply --wheel PATH to evaluate a local release artifact.")
        return "\n".join(lines)
    candidate = plan["candidate"] or "unknown version"
    lines.append(f"Update plan: {plan['verdict'].upper()} ({plan['wheel']}, {candidate})")
    lines.append(f"Running engine: {plan['current']} ({plan['installation']})")
    for key in ("blocked", "conflicts", "unknown", "notes"):
        for reason in plan[key]:
            lines.append(f"  [{key}] {reason}")
    lines.append("Steps:")
    for number, item in enumerate(plan["steps"], 1):
        lines.append(f"  {number}. {item['step']}")
        if item["command"]:
            lines.append(f"     $ {item['command']}")
    return "\n".join(lines)
