import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

import test_engine_verification_identity as engine_tests
import test_verification_identity_configuration as configuration_tests
from todo_flow import cli, verification_logs
from todo_flow.store import Conflict, Store
from todo_flow.verification_evidence import require_current


class VerificationPreflightTests(unittest.TestCase):
    setUp = engine_tests.EngineVerificationIdentityTests.setUp
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def check_command(self, label, suffix=""):
        marker = self.root / "preflight-order.txt"
        source = (
            "from pathlib import Path\n"
            f"with Path({str(marker)!r}).open('a') as stream:\n"
            f"    stream.write({label!r} + '\\n')\n"
            + suffix
        )
        return [sys.executable, "-c", source]

    def order(self):
        return (self.root / "preflight-order.txt").read_text().splitlines()

    def test_order_full_execution_unique_logs_and_cache(self):
        engine, task, workspace = self.verifier()
        self.runner.write_text(
            self.source
            + f"assert Path({str(self.root / 'preflight-order.txt')!r})"
            ".read_text().splitlines() == ['syntax', 'format']\n"
        )
        engine.config["verify_preflight"] = [
            self.check_command("syntax"),
            self.check_command("format"),
        ]
        record = engine.verify(task, workspace)
        self.assertTrue(record["ok"], record["output"])
        self.assertEqual(self.order(), ["syntax", "format"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(record["command"], engine.config["verify"])
        self.assertEqual(
            [check["stage"] for check in record["checks"]],
            ["preflight-1", "preflight-2", "final"],
        )
        references = [check["logReference"] for check in record["checks"]]
        self.assertEqual(len({ref["execution"] for ref in references}), 3)
        for ref in references:
            self.assertTrue(verification_logs.describe(self.s.path, ref)["complete"])
        self.assertEqual(record["logReference"], references[-1])
        require_current(engine.config, workspace, record["head"], record)
        self.assertEqual(engine.verify(task, workspace), record)
        self.assertEqual(self.order(), ["syntax", "format"])
        self.assertEqual(self.count(), 1)

    def test_failure_stops_later_checks_and_full_verifier_with_durable_evidence(self):
        engine, task, workspace = self.verifier()
        engine.config["verify_preflight"] = [
            self.check_command("syntax"),
            self.check_command("format", "raise SystemExit('format fixture error')\n"),
            self.check_command("never"),
        ]
        record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertEqual(self.order(), ["syntax", "format"])
        self.assertFalse(self.counter.exists())
        self.assertIn("preflight-2 failed", record["output"])
        self.assertIn("format fixture error", record["output"])
        self.assertEqual(len(record["checks"]), 2)
        failed = record["checks"][-1]
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["command"], engine.config["verify_preflight"][1])
        self.assertEqual(record["logReference"], failed["logReference"])
        logs = verification_logs.describe(self.s.path, record["logReference"])
        # Failed producers retain their bytes without claiming complete output.
        self.assertEqual(logs["format"], "file-backed-v1")
        self.assertFalse(logs["complete"])
        self.assertEqual(logs["phase"], "partial")
        self.assertEqual(logs["termination"]["state"], "confirmed")
        self.assertEqual(logs["termination"]["returncode"], 1)
        self.assertIn("format fixture error", logs["streams"]["stderr"]["text"])
        stderr = verification_logs.read_range(self.s.path, record["logReference"], "stderr")
        self.assertEqual(stderr["text"], "format fixture error\n")
        self.assertFalse(stderr["truncated"])
        self.assertEqual(json.loads(self.s.track("addition")["verification"]), record)
        path = self.s.path / "attempts" / task["attempt"] / "verification.json"
        self.assertEqual(json.loads(path.read_text()), record)
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, record["head"], record)

    def test_preflight_success_does_not_override_full_failure(self):
        engine, task, workspace = self.verifier("raise SystemExit('full fixture failure')\n")
        engine.config["verify_preflight"] = [self.check_command("syntax")]
        record = engine.verify(task, workspace)
        self.assertEqual(self.order(), ["syntax"])
        self.assertEqual(self.count(), 1)
        self.assertTrue(record["checks"][0]["ok"])
        self.assertFalse(record["checks"][-1]["ok"])
        self.assertFalse(record["ok"])
        self.assertIn("final failed", record["output"])
        self.assertIn("full fixture failure", record["output"])
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, record["head"], record)

    def test_cancellation_during_preflight_stops_process_and_never_records_success(self):
        engine, task, workspace = self.verifier()
        engine.config["verify_timeout"] = 10
        engine.config["verify_preflight"] = [
            self.check_command("started", "import time\ntime.sleep(60)\n"),
            self.check_command("never"),
        ]
        check_claim = engine.check_claim
        cancelled = False

        def cancel_after_start(current):
            nonlocal cancelled
            if not cancelled and (self.root / "preflight-order.txt").exists():
                cancelled = True
                self.s.control("addition", "cancel")
            check_claim(current)

        with patch.object(engine, "check_claim", side_effect=cancel_after_start):
            with self.assertRaisesRegex(Conflict, "Stale claim"):
                engine.verify(task, workspace)
        self.assertTrue(cancelled)
        self.assertEqual(self.order(), ["started"])
        self.assertFalse(self.counter.exists())
        engine.process_barrier(task["track"]).require_clear()
        self.assertIsNone(self.s.track("addition")["verification"])
        self.assertFalse(
            (self.s.path / "attempts" / task["attempt"] / "verification.json").exists()
        )

    def test_timeout_prevents_full_execution(self):
        engine, task, workspace = self.verifier()
        engine.config["verify_timeout"] = 1
        engine.config["verify_preflight"] = [
            self.check_command("slow", "import time\ntime.sleep(60)\n")
        ]
        record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error"], "TimeoutExpired")
        self.assertIn("preflight-1 failed", record["output"])
        self.assertFalse(self.counter.exists())
        engine.process_barrier(task["track"]).require_clear()

    def test_configuration_changes_invalidate_cache_and_current_evidence(self):
        engine, task, workspace = self.verifier()
        first = self.check_command("first")
        second = self.check_command("second")
        record = engine.verify(task, workspace)
        configurations = [[first], [first, second], [second, first], []]
        for expected, checks in enumerate(configurations, start=2):
            with self.subTest(checks=checks):
                engine.config["verify_preflight"] = checks
                with self.assertRaisesRegex(Conflict, "inputs changed"):
                    require_current(engine.config, workspace, record["head"], record)
                updated = engine.verify(task, workspace)
                self.assertTrue(updated["ok"], updated["output"])
                self.assertEqual(updated["head"], record["head"])
                self.assertEqual(updated["command"], record["command"])
                self.assertNotEqual(updated["identity"], record["identity"])
                self.assertEqual(self.count(), expected)
                self.assertEqual(engine.verify(task, workspace), updated)
                require_current(engine.config, workspace, updated["head"], updated)
                record = updated

    def test_unconfigured_and_empty_lists_preserve_existing_identity_and_execution(self):
        engine, task, workspace = self.verifier()
        record = engine.verify(task, workspace)
        self.assertTrue(record["ok"])
        self.assertNotIn("preflight", record["identity"])
        self.assertEqual([check["stage"] for check in record["checks"]], ["final"])
        engine.config["verify_preflight"] = []
        self.assertEqual(engine.verify(task, workspace), record)
        self.assertEqual(self.count(), 1)
        require_current(engine.config, workspace, record["head"], record)


class PreflightConfigurationTests(unittest.TestCase):
    setUp = configuration_tests.VerificationIdentityConfigurationTests.setUp
    args = configuration_tests.VerificationIdentityConfigurationTests.args

    def test_cli_persists_ordered_argv_and_default_remains_absent(self):
        checks = [["python3", "syntax.py"], ["ruff", "format", "--check", "."]]
        args = self.args()
        self.assertIsNone(args.verify_preflight)
        args.verify_preflight = json.dumps(checks)
        with patch("todo_flow.cli.command"), contextlib.redirect_stdout(io.StringIO()):
            cli.initialize(args)
        config = Store(self.state).config()
        self.assertEqual(config["verify_preflight"], checks)
        self.assertEqual(config["verify"], ["python3", "verify.py"])

    def test_invalid_cli_and_store_configuration_fail_before_mutation(self):
        invalid = [None, {}, "python3 syntax.py", ["python3"], [[]], [[""]], [[1]]]
        invalid += [[["python3", "bad\0argument"]]]
        for value in invalid:
            with self.subTest(value=value):
                args = self.args()
                args.verify_preflight = json.dumps(value)
                with patch("todo_flow.cli.command"), patch("todo_flow.cli.Store") as store:
                    with self.assertRaisesRegex(ValueError, "verify_preflight"):
                        cli.initialize(args)
                store.assert_not_called()
                self.assertFalse(self.state.exists())
        store = Store(self.state)
        with patch.object(store, "transaction") as transaction:
            with self.assertRaisesRegex(ValueError, "verify_preflight"):
                store.configure({"verify_preflight": "shell string"})
        transaction.assert_not_called()
