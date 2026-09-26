import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow import verification_identity as identity


class VerificationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.runner = self.workspace / "runner.py"
        self.runner.write_text("raise SystemExit(0)\n", encoding="utf-8")
        self.config = {
            "verify": ["python3", "runner.py"],
            "verify_identity": {
                "version": 1,
                "files": ["runner.py"],
                "environment": ["VERIFY_TOKEN"],
                "nonce": "initial",
            },
        }

    def capture(self, environment=None):
        return identity.capture(self.config, self.workspace, environment or {})

    def test_unchanged_inputs_match_and_runner_replacement_invalidates(self):
        first = self.capture()
        self.assertTrue(identity.matches(first, self.capture()))
        replacement = self.workspace / "replacement.py"
        replacement.write_text("raise SystemExit(1)\n", encoding="utf-8")
        replacement.replace(self.runner)
        second = self.capture()
        self.assertFalse(identity.matches(first, second))
        self.assertNotEqual(first["files"][0]["sha256"], second["files"][0]["sha256"])

    def test_environment_values_are_not_serialized_and_unset_differs_from_empty(self):
        secret = "verification-secret-that-must-not-be-stored"
        first = self.capture({"VERIFY_TOKEN": secret})
        self.assertNotIn(secret, json.dumps(first))
        self.assertFalse(identity.matches(first, self.capture({"VERIFY_TOKEN": "changed"})))
        self.assertFalse(identity.matches(self.capture(), self.capture({"VERIFY_TOKEN": ""})))
        self.assertTrue(
            identity.matches(first, self.capture({"VERIFY_TOKEN": secret, "OTHER": "x"}))
        )

    def test_actual_environment_override_is_used(self):
        self.config["verify_identity"]["environment"] = ["GIT_TERMINAL_PROMPT"]
        first = self.capture({"GIT_TERMINAL_PROMPT": "1"})
        self.assertTrue(identity.matches(first, self.capture({"GIT_TERMINAL_PROMPT": "0"})))
        self.assertEqual(identity.execution_environment({})["GIT_TERMINAL_PROMPT"], "0")

    def test_nonce_command_and_timeout_invalidate_identity(self):
        first = self.capture()
        self.config["verify_identity"]["nonce"] = "manually-invalidated"
        self.assertFalse(identity.matches(first, self.capture()))
        self.assertNotIn("manually-invalidated", json.dumps(self.capture()))
        second = self.capture()
        self.config["verify"] = ["python3", "-B", "runner.py"]
        self.assertFalse(identity.matches(second, self.capture()))
        third = self.capture()
        self.config["verify_timeout"] = 181
        self.assertFalse(identity.matches(third, self.capture()))

    def test_legacy_configuration_is_supported_but_legacy_evidence_is_not_current(self):
        del self.config["verify_identity"]
        current = self.capture()
        self.assertEqual(current["version"], 1)
        self.assertFalse(identity.matches(None, current))
        self.assertTrue(identity.matches(current, self.capture()))

    def test_unsupported_evidence_never_becomes_a_cache_hit(self):
        current = self.capture()
        for previous in ({}, {"version": 2}, {"version": True}, [], "old"):
            with self.subTest(previous=previous):
                with self.assertRaises(identity.VerificationIdentityError):
                    identity.matches(previous, current)
        with self.assertRaises(identity.VerificationIdentityError):
            identity.matches(None, {"version": 2})

    def test_invalid_configuration_is_rejected_without_reading_inputs(self):
        declarations = [
            None,
            {},
            {"version": 2},
            {"version": True},
            {"version": 1, "unknown": []},
            {"version": 1, "files": "runner.py"},
            {"version": 1, "files": [""]},
            {"version": 1, "files": ["runner.py", "runner.py"]},
            {"version": 1, "environment": ["A=B"]},
            {"version": 1, "nonce": 4},
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                config = {"verify_identity": declaration}
                with self.assertRaises(identity.VerificationIdentityError):
                    identity.validate(config)

    def test_declaration_is_detached_and_order_is_irrelevant(self):
        self.config["verify_identity"]["environment"] = ["Z", "A"]
        original = copy.deepcopy(self.config)
        declaration = identity.validate(self.config)
        declaration["files"].append("other")
        self.assertEqual(self.config, original)
        first = self.capture()
        self.config["verify_identity"]["environment"].reverse()
        self.assertTrue(identity.matches(first, self.capture()))

    def test_file_changed_during_digest_is_rejected(self):
        digest = identity._file_digest

        def replace_after_read(stream):
            result = digest(stream)
            replacement = self.workspace / "replacement.py"
            replacement.write_text("raise SystemExit(1)\n", encoding="utf-8")
            replacement.replace(self.runner)
            return result

        with patch.object(identity, "_file_digest", side_effect=replace_after_read):
            with self.assertRaises(identity.VerificationIdentityError):
                self.capture()

    def test_rewrite_and_restore_bytes_changes_identity(self):
        first = self.capture()
        original = self.runner.read_bytes()
        replacement = self.workspace / "replacement.py"
        replacement.write_bytes(original)
        replacement.replace(self.runner)
        second = self.capture()
        self.assertEqual(first["files"][0]["sha256"], second["files"][0]["sha256"])
        self.assertFalse(identity.matches(first, second))

    def test_missing_directory_and_fifo_inputs_fail_closed(self):
        self.runner.unlink()
        with self.assertRaises(identity.VerificationIdentityError):
            self.capture()
        self.runner.mkdir()
        with self.assertRaises(identity.VerificationIdentityError):
            self.capture()
        self.runner.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.runner)
            with self.assertRaises(identity.VerificationIdentityError):
                self.capture()

    def test_symlink_retargeting_invalidates_identity(self):
        target = self.workspace / "target.py"
        target.write_bytes(self.runner.read_bytes())
        self.runner.unlink()
        self.runner.symlink_to(target)
        first = self.capture()
        other = self.workspace / "other.py"
        other.write_bytes(target.read_bytes())
        self.runner.unlink()
        self.runner.symlink_to(other)
        self.assertFalse(identity.matches(first, self.capture()))
