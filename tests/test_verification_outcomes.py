import json
import unittest
from unittest.mock import patch

import test_engine_verification_identity as engine_tests
import test_integration_repair as integration_tests
from todo_flow import integration, verification_outcomes
from todo_flow.projections import Dashboard
from todo_flow.store import Conflict, encode
from todo_flow.verification import run
from todo_flow.verification_evidence import require_current


def declaration(status, reason="Checked requirement"):
    line = json.dumps({"outcome": status, "reason": reason}) + " TODO_FLOW_RESULT_V1"
    return f"print({line!r})\n"


class OutcomeContractTests(unittest.TestCase):
    def test_legacy_and_explicit_evidence_do_not_upgrade_nonpass(self):
        self.assertTrue(verification_outcomes.passed({"ok": True}))
        for record in (
            {"ok": False},
            {"ok": 1},
            {"ok": True, "outcome": None},
            {"ok": True, "outcome": "unknown"},
            {"ok": True, "outcome": "failed"},
            {"ok": True, "outcome": "inconclusive"},
            {"ok": True, "cancelled": True},
            {"ok": True, "complete": False},
            {"ok": True, "error": "Interrupted"},
            {"ok": True, "checks": [{"ok": False}]},
        ):
            with self.subTest(record=record):
                self.assertFalse(verification_outcomes.passed(record))
        self.assertEqual(
            verification_outcomes.outcome({"ok": False, "outcome": "failed"}), "failed"
        )

    def test_protocol_does_not_parse_exception_text_or_malformed_results_as_success(self):
        self.assertIsNone(verification_outcomes.read_result("AssertionError: failed"))
        for line in (
            "{}",
            "null",
            '{"outcome":"passed","reason":""}',
            '{"outcome":"unknown","reason":"test"}',
            "not-json",
            json.dumps({"outcome": "passed", "reason": "x" * 5000}),
        ):
            with self.subTest(line=line[:80]):
                with self.assertRaises(verification_outcomes.VerificationOutcomeError) as caught:
                    verification_outcomes.read_result(line + " TODO_FLOW_RESULT_V1")
                self.assertEqual(caught.exception.outcome, "inconclusive")


class OutcomeExecutionTests(unittest.TestCase):
    setUp = engine_tests.EngineVerificationIdentityTests.setUp
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def test_real_commands_distinguish_declared_failures_from_execution_failures(self):
        engine, task, workspace = self.verifier()
        cases = [
            ("", "passed"),
            (declaration("passed"), "passed"),
            (declaration("failed", "2 + 3 returned 6"), "failed"),
            (declaration("failed") + "raise SystemExit(1)\n", "failed"),
            (declaration("inconclusive", "Dependency installation failed"), "inconclusive"),
            ("raise ModuleNotFoundError('fixture dependency')\n", "inconclusive"),
            ("raise AssertionError('not a result contract')\n", "inconclusive"),
            ("raise SystemExit(17)\n", "inconclusive"),
            (declaration("passed") + "raise SystemExit(1)\n", "inconclusive"),
            ("print('{} TODO_FLOW_RESULT_V1')\n", "inconclusive"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.runner.write_text(self.source + source)
                record = engine.verify(task, workspace)
                self.assertEqual(record["outcome"], expected, record)
                self.assertEqual(record["ok"], expected == "passed")
                self.assertEqual(record["checks"][-1]["outcome"], expected)
                self.assertTrue(record["reason"])
                if expected != "passed":
                    with self.assertRaises(Conflict):
                        require_current(engine.config, workspace, record["head"], record)

    def test_environment_recovery_rechecks_identical_head_without_reusing_failure(self):
        marker = self.root / "environment-ready"
        source = (
            f"if not Path({str(marker)!r}).exists():\n"
            "    raise ModuleNotFoundError('fixture dependency unavailable')\n"
        )
        engine, task, workspace = self.verifier(source)
        first = engine.verify(task, workspace)
        self.assertEqual(first["outcome"], "inconclusive")
        second = engine.verify(task, workspace)
        self.assertEqual(second["outcome"], "inconclusive")
        self.assertEqual(self.count(), 2)
        marker.write_text("Environment restored by operator\n")
        recovered = engine.verify(task, workspace)
        self.assertEqual(recovered["outcome"], "passed")
        self.assertEqual(first["head"], recovered["head"])
        self.assertEqual(first["identity"], recovered["identity"])
        self.assertEqual(self.count(), 3)
        require_current(engine.config, workspace, recovered["head"], recovered)
        self.assertEqual(engine.verify(task, workspace), recovered)
        self.assertEqual(self.count(), 3)

    def test_explicit_nonpass_cannot_reuse_a_success_cache(self):
        engine, task, workspace = self.verifier()
        record = engine.verify(task, workspace)
        record["outcome"] = "inconclusive"
        engine.update(task, verification=encode(record))
        fresh = engine.verify(task, workspace)
        self.assertEqual(fresh["outcome"], "passed")
        self.assertEqual(self.count(), 2)
        self.assertNotEqual(record["logReference"], fresh["logReference"])

    def test_verify_and_proposal_paths_share_routing_and_publication_rules(self):
        for kind in ("verify", "work"):
            for status, successor in (
                ("passed", "review"),
                ("failed", "work"),
                ("inconclusive", "assess"),
            ):
                with self.subTest(kind=kind, status=status):
                    fixture = OutcomeExecutionTests()
                    fixture.setUp()
                    try:
                        engine, initial, workspace = fixture.verifier(
                            declaration(status, "fixture diagnosis")
                        )
                        fixture.s.finish(
                            initial,
                            {
                                "summary": "Run candidate check",
                                "next": [{"kind": kind, "purpose": "Check candidate"}],
                            },
                        )
                        task = fixture.s.claim("executor")
                        proposal = {
                            "summary": "Candidate ready",
                            "verify": True,
                            "publish": True,
                            "next": [{"kind": "review", "purpose": "Assess current goal"}],
                        }
                        with (
                            patch("todo_flow.engine.run_worker", return_value=proposal),
                            patch.object(engine, "publish") as publish,
                        ):
                            engine.execute(task)
                        record = json.loads(fixture.s.track("addition")["verification"])
                        self.assertEqual(record["outcome"], status)
                        self.assertEqual(publish.call_count, int(status == "passed"))
                        pending = fixture.s.claim("successor")
                        self.assertEqual(pending["kind"], successor)
                        if status != "passed":
                            self.assertIn("fixture diagnosis", pending["purpose"])
                        if status == "inconclusive":
                            self.assertIn(record["head"], pending["purpose"])
                            self.assertIn("ask a concrete question", pending["purpose"])
                            self.assertIn("same candidate", pending["purpose"])
                            view = Dashboard(fixture.s).activity()["items"][0]
                            self.assertEqual(view["verificationOutcome"], status)
                            self.assertEqual(view["current"]["intent"], "verification-diagnosis")
                    finally:
                        fixture.tearDown()

    def test_inconclusive_integration_preserves_candidate_and_requests_a_decision(self):
        fixture = integration_tests.IntegrationRepairTests()
        fixture.setUp()
        try:

            def unavailable(argv, workspace, *args, **kwargs):
                if "integrations" in workspace.parts:
                    raise RuntimeError("Fixture installer unavailable")
                return run(argv, workspace, *args, **kwargs)

            with patch("todo_flow.engine.run_verification", side_effect=unavailable):
                fixture.ready(conflict=False)
            current = fixture.s.track("addition")
            self.assertEqual(current["head"], fixture.before["head"])
            self.assertEqual(current["review"], fixture.before["review"])
            self.assertEqual(current["verification"], fixture.before["verification"])
            self.assertIsNone(integration.pending(current))
            decisions = fixture.s.snapshot()["decisions"]
            self.assertEqual(len(decisions), 1)
            self.assertIn("Fixture installer unavailable", json.dumps(decisions))
            self.assertIn(fixture.before["head"], json.dumps(decisions))
            self.assertIsNone(fixture.s.claim("unexpected-repair"))
        finally:
            fixture.tearDown()
