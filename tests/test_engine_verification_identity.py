import json
import os
import sys
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.store import Conflict, encode
from todo_flow.verification import run
from todo_flow.verification_identity import VerificationIdentityError


class EngineVerificationIdentityTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def verifier(self, suffix=""):
        self.s.start("addition")
        task = self.s.claim("author")
        engine = Engine(self.s)
        workspace = engine.ensure_workspace(task)
        self.runner = self.root / "runner.py"
        self.counter = self.root / "runs.txt"
        self.source = (
            "from pathlib import Path\n"
            f"counter = Path({str(self.counter)!r})\n"
            "with counter.open('a') as stream: stream.write('run\\n')\n"
        )
        self.runner.write_text(self.source + suffix)
        engine.config.update(
            verify=[sys.executable, str(self.runner)],
            verify_identity={"version": 1, "files": [str(self.runner)]},
        )
        return engine, task, workspace

    def count(self):
        return len(self.counter.read_text().splitlines())

    def test_stable_cache_reuses_execution_but_new_head_and_dirty_checkout_do_not(self):
        engine, task, workspace = self.verifier()
        first = engine.verify(task, workspace)
        self.assertTrue(first["ok"])
        self.assertEqual(first, engine.verify(task, workspace))
        self.assertEqual(self.count(), 1)
        command(["git", "commit", "--allow-empty", "-m", "New head"], workspace)
        second = engine.verify(task, workspace)
        self.assertTrue(second["ok"])
        self.assertNotEqual(first["head"], second["head"])
        self.assertEqual(first["tree"], second["tree"])
        self.assertEqual(self.count(), 2)
        (workspace / "calc.py").write_text("# dirty\n")
        with self.assertRaises(Conflict):
            engine.verify(task, workspace)
        self.assertEqual(self.count(), 2)

    def test_external_runner_replacement_invalidates_success(self):
        engine, task, workspace = self.verifier()
        first = engine.verify(task, workspace)
        self.runner.write_text(self.source + "raise SystemExit(1)\n")
        second = engine.verify(task, workspace)
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(first["head"], second["head"])
        self.assertEqual(first["command"], second["command"])
        self.assertNotEqual(first["identity"], second["identity"])
        self.assertEqual(self.count(), 2)

    def test_legacy_success_reruns_and_unknown_evidence_fails_closed(self):
        engine, task, workspace = self.verifier()
        legacy = engine.verify(task, workspace)
        del legacy["identity"]
        engine.update(task, verification=encode(legacy))
        current = engine.verify(task, workspace)
        self.assertTrue(current["ok"])
        self.assertIn("identity", current)
        self.assertEqual(self.count(), 2)
        current["identity"] = {"version": 999}
        engine.update(task, verification=encode(current))
        with self.assertRaises(VerificationIdentityError):
            engine.verify(task, workspace)
        self.assertEqual(self.count(), 2)

    def test_missing_input_never_reuses_success(self):
        engine, task, workspace = self.verifier()
        self.assertTrue(engine.verify(task, workspace)["ok"])
        self.runner.unlink()
        with self.assertRaises(VerificationIdentityError):
            engine.verify(task, workspace)
        self.assertEqual(self.count(), 1)

    def test_successful_runner_cannot_modify_declared_input_during_execution(self):
        engine, task, workspace = self.verifier(
            "Path(__file__).write_text('raise SystemExit(1)\\n')\n"
        )
        record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "VerificationIdentityError")
        self.assertIn("changed during execution", record["output"])
        self.assertEqual(self.count(), 1)
        self.assertFalse(json.loads(self.s.track("addition")["verification"])["ok"])

    def test_successful_runner_cannot_remove_declared_input(self):
        engine, task, workspace = self.verifier("Path(__file__).unlink()\n")
        record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "VerificationIdentityError")
        self.assertEqual(self.count(), 1)

    def test_change_between_identity_and_process_launch_is_rejected(self):
        engine, task, workspace = self.verifier()

        def replace_then_run(*args, **kwargs):
            self.runner.write_text(self.source + "# replaced just before launch\n")
            return run(*args, **kwargs)

        with patch("todo_flow.engine.run_verification", side_effect=replace_then_run):
            record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "VerificationIdentityError")
        self.assertEqual(self.count(), 1)

    def test_captured_environment_is_used_even_if_parent_environment_changes(self):
        engine, task, workspace = self.verifier(
            "import os\nassert os.environ['VERIFY_IDENTITY_TEST'] == 'captured'\n"
        )
        engine.config["verify_identity"]["environment"] = ["VERIFY_IDENTITY_TEST"]

        def change_parent_then_run(*args, **kwargs):
            with patch.dict(os.environ, {"VERIFY_IDENTITY_TEST": "later"}):
                return run(*args, **kwargs)

        with patch.dict(os.environ, {"VERIFY_IDENTITY_TEST": "captured"}):
            with patch("todo_flow.engine.run_verification", side_effect=change_parent_then_run):
                record = engine.verify(task, workspace)
        self.assertTrue(record["ok"])
        self.assertNotIn("captured", json.dumps(record["identity"]))
        with patch.dict(os.environ, {"VERIFY_IDENTITY_TEST": "changed"}):
            self.assertFalse(engine.verify(task, workspace)["ok"])
        self.assertEqual(self.count(), 2)
