import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

import test_engine_verification_identity as engine_tests
import test_verification_identity_configuration as configuration_tests
from todo_flow import cli
from todo_flow.adapters import command
from todo_flow.store import Conflict, Store, encode
from todo_flow.verification_evidence import require_current


class StagedVerificationTests(unittest.TestCase):
    setUp = engine_tests.EngineVerificationIdentityTests.setUp
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def related(self, engine, source="pass"):
        engine.config["verify_related"] = [[sys.executable, "-c", source]]

    def work(self):
        engine, initial, workspace = self.verifier()
        self.s.finish(
            initial,
            {"summary": "Implement", "next": [{"kind": "work", "purpose": "Change candidate"}]},
        )
        return engine, self.s.claim("author"), workspace

    def test_partial_cache_cannot_supply_full_evidence_and_policy_changes_invalidate(self):
        engine, task, workspace = self.verifier()
        self.related(engine)
        partial = engine.verify(task, workspace, scope="partial")
        self.assertTrue(partial["ok"])
        self.assertEqual(partial["scope"], "partial")
        self.assertEqual([row["stage"] for row in partial["checks"]], ["related-1"])
        self.assertFalse(self.counter.exists())
        self.assertEqual(engine.verify(task, workspace, scope="partial"), partial)
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, partial["head"], partial)
        full = engine.verify(task, workspace)
        self.assertTrue(full["ok"])
        self.assertEqual(full["scope"], "full")
        self.assertEqual(self.count(), 1)
        require_current(engine.config, workspace, full["head"], full)
        self.assertEqual(engine.verify(task, workspace), full)
        self.related(engine, "assert True")
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, full["head"], full)
        updated = engine.verify(task, workspace)
        self.assertTrue(updated["ok"])
        self.assertEqual(self.count(), 2)
        self.assertNotEqual(updated["identity"], full["identity"])
        next_partial = engine.verify(task, workspace, scope="partial")
        self.assertEqual(next_partial["scope"], "partial")
        self.assertEqual(self.count(), 2)
        self.assertNotEqual(next_partial["identity"], updated["identity"])
        engine.config["verify_related"] = []
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, updated["head"], updated)
        self.assertTrue(engine.verify(task, workspace)["ok"])
        self.assertEqual(self.count(), 3)

    def test_legacy_full_record_without_scope_remains_valid_without_related_policy(self):
        engine, task, workspace = self.verifier()
        legacy = engine.verify(task, workspace)
        del legacy["scope"]
        engine.update(task, verification=encode(legacy))
        engine.config["verify_related"] = []
        require_current(engine.config, workspace, legacy["head"], legacy)
        self.assertEqual(engine.verify(task, workspace, scope="partial"), legacy)
        self.assertEqual(self.count(), 1)
        legacy["scope"] = "partial"
        engine.update(task, verification=encode(legacy))
        self.assertEqual(engine.verify(task, workspace)["scope"], "full")
        self.assertEqual(self.count(), 2)

    def test_partial_runs_preflight_first_and_stops_after_related_failure(self):
        engine, task, workspace = self.verifier()
        marker = self.root / "preflight-marker"
        engine.config["verify_preflight"] = [
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        ]
        self.related(
            engine,
            f"from pathlib import Path; assert Path({str(marker)!r}).exists(); "
            "raise SystemExit('related defect')",
        )
        engine.config["verify_related"].append([sys.executable, "-c", "pass"])
        record = engine.verify(task, workspace, scope="partial")
        self.assertFalse(record["ok"])
        self.assertEqual([row["stage"] for row in record["checks"]], ["preflight-1", "related-1"])
        self.assertIn("related defect", record["output"])
        self.assertFalse(self.counter.exists())
        self.assertEqual(record["logReference"], record["checks"][-1]["logReference"])

    def test_recovered_proposal_requires_full_even_with_successful_partial_cache(self):
        engine, task, workspace = self.work()
        self.related(engine)
        partial = engine.verify(task, workspace, scope="partial")
        recovered = {
            "intent": {
                "task": task["id"],
                "result": {
                    "summary": "Recovered intermediate proposal",
                    "next": [{"kind": "review", "purpose": "Review recovered candidate"}],
                },
            }
        }
        with (
            patch("todo_flow.engine.proposal_application.recover", return_value=recovered),
            patch("todo_flow.engine.run_worker") as worker,
        ):
            engine.execute(task)
        worker.assert_not_called()
        full = json.loads(self.s.track("addition")["verification"])
        self.assertEqual(full["head"], partial["head"])
        self.assertEqual(full["scope"], "full")
        self.assertTrue(full["ok"])
        self.assertEqual(self.count(), 1)
        require_current(engine.config, workspace, full["head"], full)

    def test_review_worker_does_not_start_with_partial_evidence(self):
        engine, task, workspace = self.verifier()
        self.related(engine)
        engine.verify(task, workspace, scope="partial")
        self.s.finish(
            task, {"summary": "Partial only", "next": [{"kind": "review", "purpose": "Review"}]}
        )
        reviewer = self.s.claim("reviewer")
        with patch("todo_flow.engine.run_worker") as worker:
            engine.execute(reviewer)
        worker.assert_not_called()
        self.assertIsNone(self.s.track("addition")["review"])
        self.assertFalse(self.counter.exists())

    def test_explicit_verify_and_publish_requests_select_full_without_changes(self):
        for request in ("verify", "publish"):
            with self.subTest(request=request):
                fixture = StagedVerificationTests()
                fixture.setUp()
                try:
                    engine, task, workspace = fixture.work()
                    fixture.related(engine)
                    engine.verify(task, workspace, scope="partial")
                    with (
                        patch(
                            "todo_flow.engine.run_worker",
                            return_value={"summary": "Final candidate", request: True},
                        ),
                        patch.object(engine, "publish") as publish,
                    ):
                        engine.execute(task)
                    full = json.loads(fixture.s.track("addition")["verification"])
                    self.assertTrue(full["ok"])
                    self.assertEqual(full["scope"], "full")
                    self.assertEqual(fixture.count(), 1)
                    self.assertEqual(publish.call_count, int(request == "publish"))
                finally:
                    fixture.tearDown()


class StagedVerificationCostTests(unittest.TestCase):
    def sequence(self, staged, defective):
        fixture = StagedVerificationTests()
        fixture.setUp()
        try:
            engine, task, workspace = fixture.work()
            fixture.runner.write_text(
                fixture.source
                + "namespace = {}\n"
                + "exec(Path('calc.py').read_text(), namespace)\n"
                + "if namespace['add'](2, 3) != 5:\n"
                + "    print('{\"outcome\":\"failed\",'"
                " '\"reason\":\"final behavior defect\"} TODO_FLOW_RESULT_V1')\n"
                + "assert namespace['add'](2, 3) == 5, 'final behavior defect'\n"
            )
            related_counter = fixture.root / "related-runs.txt"
            if staged:
                fixture.related(
                    engine,
                    "from pathlib import Path\n"
                    "compile(Path('calc.py').read_text(), 'calc.py', 'exec')\n"
                    f"with Path({str(related_counter)!r}).open('a') as stream:\n"
                    "    stream.write('related\\n')\n",
                )
            for index in range(3):
                final = index == 2
                operator = "-" if final and defective else "+"
                content = f"# revision {index}\ndef add(a, b):\n    return a {operator} b\n"
                proposal = {
                    "summary": f"Candidate revision {index}",
                    "changes": [{"path": "calc.py", "content": content}],
                    "next": [
                        {
                            "kind": "review" if final else "work",
                            "purpose": "Review final candidate" if final else "Continue change",
                        }
                    ],
                }
                with patch("todo_flow.engine.run_worker", return_value=proposal):
                    engine.execute(task)
                record = json.loads(fixture.s.track("addition")["verification"])
                self.assertEqual(record["head"], command(["git", "rev-parse", "HEAD"], workspace))
                self.assertEqual(record["scope"], "partial" if staged and not final else "full")
                if not final:
                    self.assertTrue(record["ok"], record["output"])
                    task = fixture.s.claim("author")
                    self.assertEqual(task["kind"], "work")
            self.assertEqual(record["ok"], not defective, record["output"])
            successor = fixture.s.claim("successor")
            self.assertEqual(successor["kind"], "work" if defective else "review")
            if defective:
                self.assertIn("final behavior defect", record["output"])
                with self.assertRaises(Conflict):
                    require_current(engine.config, workspace, record["head"], record)
            else:
                require_current(engine.config, workspace, record["head"], record)
            related_count = len(related_counter.read_text().splitlines()) if staged else 0
            return fixture.count(), related_count, (workspace / "calc.py").read_text()
        finally:
            fixture.tearDown()

    def test_two_intermediate_changes_reduce_full_calls_and_keep_final_defect_detection(self):
        for defective in (False, True):
            with self.subTest(defective=defective):
                baseline = self.sequence(False, defective)
                staged = self.sequence(True, defective)
                self.assertEqual(baseline[:2], (3, 0))
                self.assertEqual(staged[:2], (1, 2))
                self.assertEqual(baseline[2], staged[2])


class RelatedConfigurationTests(unittest.TestCase):
    setUp = configuration_tests.VerificationIdentityConfigurationTests.setUp
    args = configuration_tests.VerificationIdentityConfigurationTests.args

    def test_cli_persists_related_checks_and_default_is_absent(self):
        args = self.args()
        self.assertIsNone(args.verify_related)
        checks = [[sys.executable, "-c", "pass"]]
        args.verify_related = json.dumps(checks)
        with patch("todo_flow.cli.command"), contextlib.redirect_stdout(io.StringIO()):
            cli.initialize(args)
        self.assertEqual(Store(self.state).config()["verify_related"], checks)

    def test_invalid_related_configuration_fails_before_mutation(self):
        for value in (None, {}, "shell string", ["python3"], [[]], [[""]], [[1]]):
            with self.subTest(value=value):
                args = self.args()
                args.verify_related = json.dumps(value)
                with patch("todo_flow.cli.command"), patch("todo_flow.cli.Store") as store:
                    with self.assertRaisesRegex(ValueError, "verify_related"):
                        cli.initialize(args)
                store.assert_not_called()
                self.assertFalse(self.state.exists())
        store = Store(self.state)
        with patch.object(store, "transaction") as transaction:
            with self.assertRaisesRegex(ValueError, "verify_related"):
                store.configure({"verify_related": "shell string"})
        transaction.assert_not_called()
