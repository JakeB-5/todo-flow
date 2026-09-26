import json
import os
import unittest
from unittest.mock import patch

import test_engine_verification_identity as engine_tests
from todo_flow import verification_identity


class EngineVerificationInputsTests(unittest.TestCase):
    setUp = engine_tests.EngineVerificationIdentityTests.setUp
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def test_environment_and_nonce_changes_rerun_without_storing_values(self):
        engine, task, workspace = self.verifier()
        name = "TODO_FLOW_IDENTITY_SECRET_TEST"
        engine.config["verify_identity"]["environment"] = [name]
        previous = None
        secrets = ["private-first-value", "private-second-value"]
        for expected, (value, nonce) in enumerate(
            [(secrets[0], "initial"), (secrets[1], "initial"), (secrets[1], "rotated")],
            start=1,
        ):
            with self.subTest(expected=expected):
                engine.config["verify_identity"]["nonce"] = nonce
                with patch.dict(os.environ, {name: value}):
                    record = engine.verify(task, workspace)
                    self.assertTrue(record["ok"])
                    self.assertEqual(record, engine.verify(task, workspace))
                self.assertEqual(self.count(), expected)
                if previous is not None:
                    self.assertEqual(previous["head"], record["head"])
                    self.assertEqual(previous["command"], record["command"])
                    self.assertNotEqual(previous["identity"], record["identity"])
                previous = record
                evidence = (
                    self.s.path / "attempts" / task["attempt"] / "verification.json"
                ).read_text()
                persisted = json.dumps(self.s.snapshot())
                for secret in secrets:
                    self.assertNotIn(secret, evidence)
                    self.assertNotIn(secret, persisted)

    def test_unset_and_empty_declared_environment_are_distinct(self):
        engine, task, workspace = self.verifier()
        name = "TODO_FLOW_IDENTITY_OPTIONAL_TEST"
        engine.config["verify_identity"]["environment"] = [name]
        environment = dict(os.environ)
        environment.pop(name, None)
        with patch.dict(os.environ, environment, clear=True):
            unset = engine.verify(task, workspace)
            self.assertTrue(unset["ok"])
            self.assertEqual(unset, engine.verify(task, workspace))
            with patch.dict(os.environ, {name: ""}):
                empty = engine.verify(task, workspace)
                self.assertTrue(empty["ok"])
                self.assertEqual(empty, engine.verify(task, workspace))
        self.assertNotEqual(unset["identity"], empty["identity"])
        self.assertEqual(self.count(), 2)

    def test_declared_external_input_change_invalidates_success(self):
        engine, task, workspace = self.verifier()
        external_input = self.root / "verification-input.txt"
        external_input.write_text("pass")
        self.runner.write_text(
            self.source + f"assert Path({str(external_input)!r}).read_text() == 'pass'\n"
        )
        engine.config["verify_identity"]["files"].append(str(external_input))
        first = engine.verify(task, workspace)
        self.assertTrue(first["ok"])
        self.assertEqual(first, engine.verify(task, workspace))
        self.assertEqual(self.count(), 1)
        external_input.write_text("fail")
        second = engine.verify(task, workspace)
        self.assertFalse(second["ok"])
        self.assertEqual(first["head"], second["head"])
        self.assertEqual(first["command"], second["command"])
        self.assertNotEqual(first["identity"], second["identity"])
        self.assertEqual(self.count(), 2)

    def test_change_before_second_identity_observation_prevents_launch(self):
        engine, task, workspace = self.verifier()
        capture = verification_identity.capture
        observations = 0

        def replace_before_launch(*args, **kwargs):
            nonlocal observations
            observations += 1
            if observations == 2:
                self.runner.write_text(self.source + "# changed before execution\n")
            return capture(*args, **kwargs)

        with patch.object(verification_identity, "capture", side_effect=replace_before_launch):
            with patch("todo_flow.engine.run_verification") as launch:
                record = engine.verify(task, workspace)
        launch.assert_not_called()
        self.assertFalse(self.counter.exists())
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "VerificationIdentityError")
        self.assertIn("changed before execution", record["output"])
        persisted = json.loads(self.s.track("addition")["verification"])
        self.assertEqual(record, persisted)
