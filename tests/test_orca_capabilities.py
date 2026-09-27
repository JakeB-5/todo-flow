import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from todo_flow.orca_capabilities import inspect_schema, probe_native_contract


# Reduced projection of actual `orca agent-context --json` output captured on
# 2026-09-26: command/path/flags are public fields. Unrelated commands and prose
# are omitted. This is not a fabricated capability response from the runtime.
PUBLIC_SCHEMA = {
    "schemaVersion": 1,
    "commands": [
        {
            "command": "orchestration worker-show",
            "path": ["orchestration", "worker-show"],
            "flags": ["help", "json", "pairing-code", "environment", "dispatch"],
        },
        {
            "command": "orchestration worker-read",
            "path": ["orchestration", "worker-read"],
            "flags": [
                "help",
                "json",
                "pairing-code",
                "environment",
                "dispatch",
                "source",
                "cursor",
                "limit",
            ],
        },
        {
            "command": "terminal send",
            "path": ["terminal", "send"],
            "flags": [
                "help",
                "json",
                "pairing-code",
                "environment",
                "terminal",
                "text",
                "enter",
                "interrupt",
                "wait-submit",
                "retry-request",
            ],
        },
    ],
}


class OrcaCapabilityTests(unittest.TestCase):
    def probe(self, payload=PUBLIC_SCHEMA, returncode=0, stderr=""):
        output = json.dumps(payload) if not isinstance(payload, str) else payload
        result = subprocess.CompletedProcess([], returncode, output, stderr)
        with patch("todo_flow.orca_capabilities.subprocess.run", return_value=result) as run:
            report = probe_native_contract("selected-orca", "/workspace")
        run.assert_called_once_with(
            ["selected-orca", "agent-context", "--json"],
            cwd="/workspace",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return report

    def test_public_advertisements_do_not_authorize_native_execution(self):
        report = self.probe()
        self.assertTrue(report["advertised"]["worker_lookup"])
        self.assertTrue(report["advertised"]["worker_output_pages"])
        self.assertTrue(report["advertised"]["prompt_retry"])
        self.assertFalse(report["advertised"]["managed_agent_worktree"])
        self.assertFalse(report["native_ready"])
        self.assertEqual(report["status"], "native_contract_unverified")
        self.assertIn("read_only_agent_policy", report["unverified_contracts"])
        self.assertIn("complete_structured_proposal", report["unverified_contracts"])
        self.assertEqual(len(report["schema_sha256"]), 64)
        json.dumps(report)

    def test_missing_retry_flag_is_not_recovery_support(self):
        schema = copy.deepcopy(PUBLIC_SCHEMA)
        schema["commands"][-1]["flags"].remove("retry-request")
        self.assertFalse(inspect_schema(schema)["prompt_retry"])

    def test_old_cli_is_distinct_from_transport_failure(self):
        old = self.probe("", 1, "error: unknown command 'agent-context'")
        failed = self.probe("", 1, "runtime unreachable")
        self.assertEqual(old["status"], "discovery_command_unsupported")
        self.assertEqual(failed["status"], "discovery_failed")
        self.assertFalse(old["native_ready"])
        self.assertFalse(failed["native_ready"])

    def test_missing_binary_and_timeout_never_try_another_command(self):
        for error, status in (
            (FileNotFoundError(), "cli_missing"),
            (PermissionError(), "discovery_failed"),
            (subprocess.TimeoutExpired("orca", 10), "discovery_failed"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch("todo_flow.orca_capabilities.subprocess.run", side_effect=error) as run:
                    report = probe_native_contract("selected-orca", "/workspace")
                run.assert_called_once()
                self.assertEqual(report["status"], status)
                self.assertFalse(report["native_ready"])

    def test_truncated_unknown_and_malformed_schemas_fail_closed(self):
        duplicate = copy.deepcopy(PUBLIC_SCHEMA)
        duplicate["commands"].append(duplicate["commands"][0])
        bad_flags = copy.deepcopy(PUBLIC_SCHEMA)
        bad_flags["commands"][0]["flags"] = "dispatch"
        bad_path = copy.deepcopy(PUBLIC_SCHEMA)
        bad_path["commands"][0]["path"] = ["terminal", "create"]
        for payload in (
            '{"schemaVersion":',
            {"schemaVersion": 2, "commands": []},
            {"schemaVersion": True, "commands": []},
            {"schemaVersion": 1, "commands": {}},
            [],
            duplicate,
            bad_flags,
            bad_path,
        ):
            with self.subTest(payload=payload):
                report = self.probe(payload)
                self.assertEqual(report["status"], "schema_unrecognized")
                self.assertFalse(report["native_ready"])
                self.assertEqual(report["advertised"], {})
