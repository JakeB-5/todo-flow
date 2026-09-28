"""Artifact evidence follows real Engine executions and confirmed process exit."""

import json
import unittest
from unittest.mock import Mock, patch

import test_engine_verification_identity as engine_tests
from todo_flow import verification_artifacts as artifacts
from todo_flow.adapters import command
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.store import Conflict
from todo_flow.verification import VerificationCleanupError


GENERATED = (
    ".venv/pyvenv.cfg",
    "dist/example.whl",
    ".ruff_cache/version/cache",
    "src/example.egg-info/PKG-INFO",
)


class EngineVerificationArtifactTests(unittest.TestCase):
    setUp = engine_tests.EngineVerificationIdentityTests.setUp
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def prepare(self, suffix=""):
        engine, task, workspace = self.verifier()
        ignore = workspace / ".gitignore"
        ignore.write_text(ignore.read_text() + ".venv/\ndist/\n.ruff_cache/\n*.egg-info/\n")
        command(["git", "add", ".gitignore"], workspace)
        command(["git", "commit", "-m", "Ignore fixture verification artifacts"], workspace)
        folder = self.s.path / "verification-artifacts"
        self.runner.write_text(
            self.source
            + "import json\n"
            + f"receipts = list(Path({str(folder)!r}).glob('*.json'))\n"
            + "assert len(receipts) == 1\n"
            + "receipt = json.loads(receipts[0].read_text())\n"
            + "assert receipt['phase'] == 'running'\n"
            + f"for name in {GENERATED!r}:\n"
            + "    path = Path(name)\n"
            + "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            + "    path.write_text('generated')\n"
            + suffix
        )
        return engine, task, workspace

    def receipt_path(self, workspace):
        return artifacts.manifest_path(
            self.s.path, "addition", artifacts.checkout_identity(workspace)
        )

    def receipt(self, workspace):
        return json.loads(self.receipt_path(workspace).read_text())

    def removable(self, workspace):
        return artifacts.removable_files(self.s.path, "addition", workspace)

    def test_real_engine_execution_records_artifacts_and_cache_preserves_evidence(self):
        engine, task, workspace = self.prepare()
        verification = engine.verify(task, workspace)
        self.assertTrue(verification["ok"], verification["output"])
        record = self.receipt(workspace)
        self.assertEqual(record["phase"], "complete")
        self.assertEqual(record["command"], engine.config["verify"])
        self.assertEqual(record["head"], verification["head"])
        self.assertEqual(record["execution"]["track"], task["track"])
        self.assertEqual(record["execution"]["attempt"], task["attempt"])
        self.assertEqual(set(self.removable(workspace)), set(GENERATED))
        before = self.receipt_path(workspace).read_bytes()
        self.assertEqual(engine.verify(task, workspace), verification)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.receipt_path(workspace).read_bytes(), before)
        self.assertEqual(artifacts.reclaim(self.s.path, "addition", workspace), sorted(GENERATED))
        self.assertTrue(all(not (workspace / name).exists() for name in GENERATED))
        self.assertTrue(self.receipt_path(workspace).exists())
        engine.process_barrier(task["track"]).require_clear()

    def test_confirmed_nonzero_exit_retains_artifact_proof_and_failure_diagnostics(self):
        engine, task, workspace = self.prepare("raise SystemExit('fixture failure')\n")
        verification = engine.verify(task, workspace)
        self.assertFalse(verification["ok"])
        self.assertIn("fixture failure", verification["output"])
        self.assertEqual(self.receipt(workspace)["phase"], "complete")
        self.assertEqual(set(self.removable(workspace)), set(GENERATED))
        engine.process_barrier(task["track"]).require_clear()

    def test_confirmed_timeout_retains_proof_without_becoming_verification_success(self):
        engine, task, workspace = self.prepare("import time\ntime.sleep(60)\n")
        engine.config["verify_timeout"] = 2
        verification = engine.verify(task, workspace)
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["error"], "TimeoutExpired")
        self.assertEqual(self.receipt(workspace)["phase"], "complete")
        self.assertEqual(set(self.removable(workspace)), set(GENERATED))
        engine.process_barrier(task["track"]).require_clear()

    def test_cache_hit_does_not_adopt_user_edit_to_generated_file(self):
        engine, task, workspace = self.prepare()
        first = engine.verify(task, workspace)
        self.assertTrue(first["ok"], first["output"])
        before = self.receipt_path(workspace).read_bytes()
        path = workspace / GENERATED[0]
        path.write_text("user modification")
        self.assertEqual(engine.verify(task, workspace), first)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.receipt_path(workspace).read_bytes(), before)
        with self.assertRaisesRegex(Conflict, "changed user files"):
            artifacts.reclaim(self.s.path, "addition", workspace)
        self.assertEqual(path.read_text(), "user modification")
        self.assertTrue((workspace / GENERATED[1]).exists())

    def test_preexisting_environment_file_is_not_adopted_by_real_verifier(self):
        engine, task, workspace = self.prepare()
        path = workspace / GENERATED[0]
        path.parent.mkdir(parents=True)
        path.write_text("preexisting environment")
        verification = engine.verify(task, workspace)
        self.assertTrue(verification["ok"], verification["output"])
        record = self.receipt(workspace)
        self.assertNotIn(GENERATED[0], record["files"])
        self.assertEqual(set(record["files"]), set(GENERATED[1:]))
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            artifacts.reclaim(self.s.path, "addition", workspace)
        self.assertTrue(all((workspace / name).exists() for name in GENERATED))

    def test_tracked_modification_still_invalidates_verification(self):
        engine, task, workspace = self.prepare("Path('calc.py').write_text('# changed\\n')\n")
        verification = engine.verify(task, workspace)
        self.assertFalse(verification["ok"])
        self.assertIn("existing changes", verification["output"])
        self.assertEqual(self.receipt(workspace)["phase"], "complete")
        self.assertNotIn("calc.py", self.receipt(workspace)["files"])
        self.assertEqual((workspace / "calc.py").read_text(), "# changed\n")

    def test_cancel_after_artifact_creation_retains_proof_without_verification_result(self):
        engine, task, workspace = self.prepare("import time\ntime.sleep(60)\n")
        engine.config["verify_timeout"] = 10
        check_claim = engine.check_claim
        cancelled = False

        def cancel_after_artifacts(current):
            nonlocal cancelled
            if not cancelled and all((workspace / name).exists() for name in GENERATED):
                cancelled = True
                self.s.control("addition", "cancel")
            check_claim(current)

        with patch.object(engine, "check_claim", side_effect=cancel_after_artifacts):
            with self.assertRaisesRegex(Conflict, "Stale claim"):
                engine.verify(task, workspace)
        self.assertTrue(cancelled)
        engine.process_barrier(task["track"]).require_clear()
        self.assertEqual(self.receipt(workspace)["phase"], "complete")
        self.assertEqual(set(self.removable(workspace)), set(GENERATED))
        self.assertIsNone(self.s.track("addition")["verification"])
        self.assertFalse(
            (self.s.path / "attempts" / task["attempt"] / "verification.json").exists()
        )

    def assert_uncertain_run(self, stage, error):
        engine, task, workspace = self.prepare()
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        if stage == "poll":
            process.poll.side_effect = error
        elif stage == "stop":
            process.stop.side_effect = error

        def launch(*args, **kwargs):
            self.assertEqual(self.receipt(workspace)["phase"], "running")
            path = workspace / GENERATED[1]
            path.parent.mkdir(parents=True)
            path.write_text("unconfirmed output")
            if stage == "launch":
                raise error
            return process

        with patch("todo_flow.supervised_process.SupervisedProcess", side_effect=launch):
            with self.assertRaisesRegex(type(error), "unconfirmed"):
                engine.verify(task, workspace)
        self.assertEqual(self.receipt(workspace)["phase"], "running")
        self.assertEqual(self.receipt(workspace)["files"], {})
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            artifacts.reclaim(self.s.path, "addition", workspace)
        self.assertEqual((workspace / GENERATED[1]).read_text(), "unconfirmed output")
        self.assertIsNone(self.s.track("addition")["verification"])
        if stage != "launch":
            process.stop.assert_called_once_with()

    def test_uncertain_launch_does_not_complete_artifact_receipt(self):
        self.assert_uncertain_run("launch", ProcessBarrierError("unconfirmed launch"))

    def test_process_barrier_error_does_not_complete_artifact_receipt(self):
        self.assert_uncertain_run("poll", ProcessBarrierError("unconfirmed process"))

    def test_unconfirmed_stop_does_not_complete_artifact_receipt(self):
        self.assert_uncertain_run("stop", VerificationCleanupError("unconfirmed stop"))
