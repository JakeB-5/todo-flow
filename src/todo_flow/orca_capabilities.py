"""Read Orca's public command schema without creating a workspace or session.

Command advertisement is not a runtime capability guarantee. In particular, the
observed schema does not establish a read-only native agent policy or a complete
structured proposal transport. Keep native execution disabled until those
contracts have a reviewed binding; never infer them from command names.
"""

import hashlib
import json
import subprocess


# Public command/flag names observed via `orca agent-context --json` on
# 2026-09-26. These are CLI advertisements, not invented runtime capability keys.
ADVERTISEMENTS = {
    "managed_agent_worktree": ("worktree create", {"agent", "setup", "base-branch"}),
    "worker_start_retry": ("orchestration worker-start", {"retry-request"}),
    "worker_lookup": ("orchestration worker-show", {"dispatch"}),
    "worker_output_pages": ("orchestration worker-read", {"dispatch", "cursor", "source"}),
    "terminal_lookup": ("terminal list", {"worktree"}),
    "prompt_retry": ("terminal send", {"retry-request", "wait-submit"}),
}


def inspect_schema(payload):
    """Validate the public schema and return advertisements, never authorization."""
    if not isinstance(payload, dict):
        raise ValueError("Orca command schema must be an object")
    if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ValueError("Unsupported Orca command schema version")
    commands = payload.get("commands")
    if not isinstance(commands, list):
        raise ValueError("Orca commands must be an array")
    indexed = {}
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError("Invalid Orca command entry")
        name = command.get("command")
        path = command.get("path")
        flags = command.get("flags")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(path, list)
            or not path
            or not all(isinstance(part, str) and part for part in path)
            or " ".join(path) != name
            or not isinstance(flags, list)
            or not all(isinstance(flag, str) for flag in flags)
            or name in indexed
        ):
            raise ValueError("Invalid or duplicate Orca command declaration")
        indexed[name] = set(flags)
    return {
        feature: name in indexed and required <= indexed[name]
        for feature, (name, required) in ADVERTISEMENTS.items()
    }


def probe_native_contract(cli, workspace):
    """Return serializable evidence from one read-only, host-local CLI call.

    This function never selects a fallback or performs a launch. Call it only
    before launch intent exists; creation uncertainty belongs to recovery, not
    to discovery. The caller must persist this evidence with its route decision.
    """
    report = {
        "cli": cli,
        "source": "agent-context --json",
        "native_ready": False,
        "status": "discovery_failed",
        "advertised": {},
        "unverified_contracts": [
            "read_only_agent_policy",
            "complete_structured_proposal",
            "runtime_request_and_session_recovery",
        ],
    }
    try:
        completed = subprocess.run(
            [cli, "agent-context", "--json"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        report["status"] = "cli_missing"
        return report
    except (OSError, subprocess.SubprocessError) as error:
        report["error_type"] = type(error).__name__
        return report
    report["returncode"] = completed.returncode
    if completed.returncode:
        diagnostic = (completed.stdout + "\n" + completed.stderr).lower()
        # Only explicit rejection identifies a pre-discovery CLI. Transport,
        # permission and runtime errors must not be relabeled as old versions.
        if "unknown command" in diagnostic and "agent-context" in diagnostic:
            report["status"] = "discovery_command_unsupported"
        return report
    report["schema_sha256"] = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
    try:
        payload = json.loads(completed.stdout)
        report["advertised"] = inspect_schema(payload)
    except (ValueError, TypeError) as error:
        report["status"] = "schema_unrecognized"
        report["error_type"] = type(error).__name__
        return report
    report["schema_version"] = payload["schemaVersion"]
    report["status"] = "native_contract_unverified"
    return report
