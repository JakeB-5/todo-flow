import json
import os
import unittest
from unittest.mock import patch

import test_engine_verification_identity as fixtures
from todo_flow import verification_identity


class EngineIdentityRegressionTests(unittest.TestCase):
    setUp = fixtures.EngineVerificationIdentityTests.setUp
    tearDown = fixtures.EngineVerificationIdentityTests.tearDown
    verifier = fixtures.EngineVerificationIdentityTests.verifier
    count = fixtures.EngineVerificationIdentityTests.count

    def test_declared_environment_and_nonce_invalidate_then_reuse_success(self):
        engine, task, workspace = self.verifier()
        declaration = engine.config["verify_identity"]
        declaration["environment"] = ["VERIFY_IDENTITY_TEST"]
        records = []
        for value, nonce in (("first", "one"), ("second", "one"), ("second", "two")):
            with self.subTest(value=value, nonce=nonce):
                declaration["nonce"] = nonce
                with patch.dict(os.environ, {"VERIFY_IDENTITY_TEST": value}):
                    record = engine.verify(task, workspace)
                    self.assertTrue(record["ok"])
                    records.append(record)
                    self.assertEqual(engine.verify(task, workspace), record)
                self.assertEqual(self.count(), len(records))
        self.assertEqual(len({record["head"] for record in records}), 1)
        self.assertTrue(all(record["command"] == records[0]["command"] for record in records))
        self.assertNotEqual(records[0]["identity"], records[1]["identity"])
        self.assertNotEqual(records[1]["identity"], records[2]["identity"])

    def test_change_after_first_capture_prevents_process_launch(self):
        engine, task, workspace = self.verifier()
        capture = verification_identity.capture
        observations = 0

        def capture_then_replace(*args, **kwargs):
            nonlocal observations
            identity = capture(*args, **kwargs)
            observations += 1
            if observations == 1:
                self.runner.write_text(self.source + "# changed after initial capture\n")
            return identity

        with patch("todo_flow.engine.verification_identity.capture", capture_then_replace):
            with patch("todo_flow.engine.run_verification") as launch:
                record = engine.verify(task, workspace)
        launch.assert_not_called()
        self.assertFalse(self.counter.exists())
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "VerificationIdentityError")
        self.assertIn("changed before execution", record["output"])
        persisted = json.loads(self.s.track("addition")["verification"])
        self.assertEqual(persisted, record)

    def test_environment_plaintext_is_absent_from_persisted_verification_evidence(self):
        engine, task, workspace = self.verifier(
            "import os\nassert os.environ['VERIFY_IDENTITY_SECRET']\n"
        )
        engine.config["verify_identity"]["environment"] = ["VERIFY_IDENTITY_SECRET"]
        secret = "identity-regression-secret-72ea915d"
        with patch.dict(os.environ, {"VERIFY_IDENTITY_SECRET": secret}):
            record = engine.verify(task, workspace)
        self.assertTrue(record["ok"])
        self.assertEqual(self.count(), 1)
        environment = record["identity"]["environment"]
        self.assertEqual([item["name"] for item in environment], ["VERIFY_IDENTITY_SECRET"])
        self.assertEqual(len(environment[0]["sha256"]), 64)
        persisted = self.s.path / "attempts" / task["attempt"] / "verification.json"
        self.assertEqual(json.loads(persisted.read_text()), record)
        self.assertNotIn(secret, json.dumps(record))
        self.assertNotIn(secret, json.dumps(self.s.snapshot()))
        for path in self.s.path.rglob("*.json"):
            with self.subTest(path=str(path.relative_to(self.s.path))):
                self.assertNotIn(secret.encode(), path.read_bytes())
