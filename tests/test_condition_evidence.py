"""Exercise optional references through real verification, review and adoption."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_engine_verification_identity as engine_tests
from todo_flow import condition_evidence
from todo_flow.adapters import command
from todo_flow.projections import Dashboard
from todo_flow.store import Conflict, encode
from todo_flow.worker import SCHEMA, codex_schema, worker_input


class ConditionEvidenceTests(unittest.TestCase):
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    tearDown = engine_tests.EngineVerificationIdentityTests.tearDown

    def setUp(self):
        engine_tests.EngineVerificationIdentityTests.setUp(self)
        self.engine, author, self.workspace = self.verifier("print('verified original')\n")
        self.record = self.engine.verify(author, self.workspace)
        self.assertTrue(self.record["ok"], self.record["output"])
        self.s.finish(
            author,
            {"summary": "ready", "next": [{"kind": "review", "purpose": "independent review"}]},
        )
        self.task = self.s.claim("reviewer")
        self.assertNotEqual(author["attempt"], self.task["attempt"])
        self.track = self.s.track("addition")
        artifacts = condition_evidence.available(self.s.path, self.track)
        self.reference = {**artifacts[0], "conditionId": "sum"}
        self.dashboard = Dashboard(self.s)

    def result(self, verdict="met"):
        return {
            "summary": "Independent semantic assessment",
            "verdict": verdict,
            "conditions": [
                {
                    "id": "sum",
                    "verdict": verdict,
                    "evidence": "Verification output supports this assessment",
                    "evidenceRefs": [copy.deepcopy(self.reference)],
                }
            ],
        }

    def status(self):
        value = self.dashboard.evidence("addition", "review")["value"]
        return value["conditions"][0]["evidenceIntegrity"]["status"]

    def test_reference_registration_query_handoff_and_adoption(self):
        result = self.result()
        self.engine.record_review(self.task, result, expected_head=self.track["head"])
        persisted = json.loads(self.s.track("addition")["review"])
        self.assertEqual(persisted["conditions"], result["conditions"])
        self.assertEqual(self.status(), "integrity-checked")
        detail = self.dashboard.detail("addition")
        self.assertEqual(
            detail["review"]["conditions"][0]["evidenceIntegrity"]["status"],
            "integrity-checked",
        )
        queried = self.dashboard.evidence("addition", "verification")["value"]
        self.assertEqual(queried["evidenceArtifacts"][0]["sha256"], self.reference["sha256"])
        folder = self.root / "evidence-handoff"
        folder.mkdir()
        payload = worker_input(self.engine.context(self.task, self.workspace), folder)
        offered = json.loads(Path(payload["paths"]["evidence_artifacts"]).read_text())
        review = json.loads(Path(payload["paths"]["review"]).read_text())
        self.assertEqual({**offered[0], "conditionId": "sum"}, self.reference)
        self.assertEqual(review["conditions"][0]["evidenceRefs"], [self.reference])
        self.assertEqual(
            review["conditions"][0]["evidenceIntegrity"]["status"], "integrity-checked"
        )
        self.assertEqual(self.engine.gate(self.task)["head"], self.track["head"])
        properties = SCHEMA["properties"]["conditions"]["items"]["properties"]
        self.assertIn("evidenceRefs", properties)
        strict = codex_schema()["properties"]["conditions"]["anyOf"][0]
        optional = strict["items"]["properties"]["evidenceRefs"]
        self.assertIn({"type": "null"}, optional["anyOf"])

    def test_legacy_prose_and_omitted_null_empty_references_remain_prose(self):
        for mode in ("omitted", "null", "empty"):
            with self.subTest(mode=mode):
                result = self.result()
                row = result["conditions"][0]
                row["evidence"] = "nonexistent-proof.json:42"
                if mode == "omitted":
                    del row["evidenceRefs"]
                else:
                    row["evidenceRefs"] = None if mode == "null" else []
                self.engine.record_review(self.task, result)
                self.assertEqual(self.status(), "prose-only")
                self.engine.gate(self.task)
                view = self.engine.context(self.task, self.workspace)["review"]
                self.assertEqual(view["conditions"][0]["evidence"], row["evidence"])
                self.assertEqual(view["conditions"][0]["evidenceIntegrity"]["status"], "prose-only")

    def test_foreign_incomplete_and_unsupported_references_are_rejected(self):
        mutations = [
            ("conditionId", "other"),
            ("documentRevision", self.track["revision"] - 1),
            ("head", "0" * 40),
            ("sha256", "0" * 64),
            ("path", str(self.root / "missing-proof")),
            ("path", str(self.s.path / ".." / "outside.log")),
            ("stream", "other"),
            ("version", 999),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                result = self.result()
                result["conditions"][0]["evidenceRefs"][0][key] = value
                with self.assertRaises(ValueError):
                    self.engine.record_review(self.task, result)
        for key in self.reference:
            with self.subTest(missing=key):
                result = self.result()
                del result["conditions"][0]["evidenceRefs"][0][key]
                with self.assertRaises(ValueError):
                    self.engine.record_review(self.task, result)
        for key in ("track", "attempt", "execution", "manifest", "version"):
            with self.subTest(log_field=key):
                result = self.result()
                ref = result["conditions"][0]["evidenceRefs"][0]
                ref["logReference"][key] = 999 if key == "version" else "other"
                with self.assertRaises(ValueError):
                    self.engine.record_review(self.task, result)
        self.assertIsNone(self.s.track("addition")["review"])

    def test_old_real_execution_cannot_be_adopted_after_new_verification(self):
        self.runner.write_text(self.source + "print('second verification')\n")
        replacement = self.engine.verify(self.task, self.workspace)
        self.assertTrue(replacement["ok"])
        self.assertNotEqual(replacement["logReference"], self.record["logReference"])
        with self.assertRaises(ValueError):
            self.engine.record_review(self.task, self.result())

    def test_changed_original_invalidates_record_query_and_gate(self):
        self.engine.record_review(self.task, self.result())
        Path(self.reference["path"]).write_bytes(b"changed original")
        self.assertEqual(self.status(), "invalid")
        with self.assertRaises(ValueError):
            self.engine.record_review(self.task, self.result())
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        self.assertNotIn(
            self.reference["path"],
            [x["path"] for x in condition_evidence.available(self.s.path, self.track)],
        )

    def test_lost_original_invalidates_existing_review(self):
        self.engine.record_review(self.task, self.result())
        Path(self.reference["path"]).unlink()
        self.assertEqual(self.status(), "invalid")
        with self.assertRaises(ValueError):
            self.engine.record_review(self.task, self.result())
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        view = self.engine.context(self.task, self.workspace)["review"]
        self.assertEqual(view["conditions"][0]["evidenceIntegrity"]["status"], "invalid")

    def test_symlink_to_identical_bytes_is_not_an_original(self):
        path = Path(self.reference["path"])
        outside = self.root / "copied-output"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.engine.record_review(self.task, self.result())

    def test_interrupted_missing_and_unsupported_receipts_fail_closed(self):
        self.engine.record_review(self.task, self.result())
        manifest = Path(self.reference["logReference"]["manifest"])
        original = manifest.read_bytes()
        mutations = (
            {"phase": "running", "complete": False},
            {"phase": "error", "complete": False},
            {"version": 999},
            {"files": {}},
        )
        for update in mutations:
            with self.subTest(update=update):
                record = json.loads(original)
                record.update(update)
                manifest.write_text(encode(record))
                self.assertEqual(self.status(), "invalid")
                with self.assertRaises(ValueError):
                    self.engine.record_review(self.task, self.result())
                with self.assertRaises(Conflict):
                    self.engine.gate(self.task)
                manifest.write_bytes(original)
        manifest.unlink()
        self.assertEqual(self.status(), "invalid")
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        manifest.write_bytes(original)
        self.assertEqual(self.status(), "integrity-checked")
        with patch("todo_flow.verification_logs._termination", return_value={"state": "unknown"}):
            self.assertEqual(self.status(), "invalid")
            with self.assertRaises(ValueError):
                self.engine.record_review(self.task, self.result())
            with self.assertRaises(Conflict):
                self.engine.gate(self.task)

    def test_interrupted_review_registration_does_not_create_valid_evidence(self):
        with patch.object(self.engine, "update", side_effect=OSError("interrupted write")):
            with self.assertRaises(OSError):
                self.engine.record_review(self.task, self.result())
        self.assertIsNone(self.s.track("addition")["review"])
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)

    def test_integrity_never_overrides_semantics_or_existing_gates(self):
        self.engine.record_review(self.task, self.result("unmet"))
        self.assertEqual(self.status(), "integrity-checked")
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        for mode in ("missing", "duplicate", "contradictory"):
            with self.subTest(mode=mode):
                result = self.result()
                if mode == "missing":
                    result["conditions"] = []
                elif mode == "duplicate":
                    result["conditions"] *= 2
                else:
                    result["conditions"][0]["verdict"] = "unmet"
                with self.assertRaises(ValueError):
                    self.engine.record_review(self.task, result)
        self.engine.record_review(self.task, self.result())
        self.engine.update(self.task, review=None)
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        self.engine.record_review(self.task, self.result())
        (self.workspace / "untracked-evidence-test").write_text("dirty")
        with self.assertRaises(Conflict):
            self.engine.record_review(self.task, self.result())
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
        (self.workspace / "untracked-evidence-test").unlink()
        command(["git", "commit", "--allow-empty", "-m", "Concurrent head"], self.workspace)
        with self.assertRaises(Conflict):
            self.engine.record_review(self.task, self.result(), expected_head=self.track["head"])
        with self.assertRaises(Conflict):
            self.engine.gate(self.task)
