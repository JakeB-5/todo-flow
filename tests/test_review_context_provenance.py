import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from todo_flow.engine import Engine
from todo_flow.worker import worker_input


class ReviewContextProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = Mock()
        self.store.path = self.root / "state"
        self.store.config.return_value = {
            "repo": str(self.workspace),
            "base": "main",
            "endpoint": "land",
            "context_patterns": ["*.py"],
            "writable_patterns": ["*.py"],
        }
        self.document = {
            "id": "example",
            "goal": "Review the candidate",
            "conditions": [{"id": "behavior", "text": "Required behavior"}],
        }
        self.verification = {"head": "candidate", "ok": True, "command": ["check"]}
        self.track = {
            "id": "example",
            "revision": 3,
            "document": json.dumps(self.document),
            "head": "candidate",
            "verification": json.dumps(self.verification),
            "review": None,
            "landing": None,
        }
        self.store.track.return_value = self.track
        self.snapshot = {
            "tasks": [],
            "results": [],
            "decisions": [
                {"id": "decision", "track": "example", "status": "answered", "answer": "Keep X"}
            ],
            "watches": [],
            "findings": [
                {"id": "finding", "track": "example", "body": '{"observation":"Unresolved"}'},
                {"id": "incoming", "track": "other", "body": '{"target":"example"}'},
            ],
        }
        self.store.snapshot.return_value = self.snapshot
        self.add_result(
            "review-old", "review", 1, {"summary": "EARLIER_REVIEW", "verdict": "unmet"}
        )
        for index in range(8):
            self.add_result(
                f"work-{index}",
                "work",
                index + 2,
                {
                    "summary": "AUTHOR_SELF_APPROVAL",
                    "kind": "review",
                    "attempt": "forged-attempt",
                    "changes": [{"path": "code.py", "content": "AUTHOR_SOURCE_BODY"}],
                },
            )
        self.add_result("foreign", "review", 20, {"summary": "FOREIGN_TRACK"}, track="other")
        # A snapshot's table order is not a chronological contract.
        self.snapshot["results"].reverse()
        self.engine = Engine(self.store)
        self.logs = {"reference": "host-log"}
        self.artifacts = [{"head": "candidate", "path": "sealed-output"}]

    def add_result(self, task_id, kind, created, body, track="example"):
        self.snapshot["tasks"].append({"id": task_id, "track": track, "kind": kind})
        self.snapshot["results"].append(
            {
                "id": "result-" + task_id,
                "task": task_id,
                "attempt": "attempt-" + task_id,
                "created": created,
                "body": json.dumps(body),
            }
        )

    def context(self, kind="review"):
        task = {
            "id": "current-task",
            "track": "example",
            "kind": kind,
            "purpose": "Inspect candidate",
            "attempt": "fresh-attempt",
        }
        with (
            patch("todo_flow.engine.command", return_value="CANDIDATE_DIFF"),
            patch("todo_flow.engine.log_view", return_value=self.logs),
            patch("todo_flow.engine.condition_evidence.available", return_value=self.artifacts),
            patch("todo_flow.engine.condition_evidence.review_view", return_value=None),
        ):
            return self.engine.context(task, self.workspace)

    def test_author_claims_are_only_supplementary_with_stored_provenance(self):
        context = self.context()
        self.assertNotIn("recent_results", context)
        supplementary = context.pop("supplementary_results")
        self.assertNotIn("AUTHOR_SELF_APPROVAL", json.dumps(context))
        self.assertNotIn("AUTHOR_SOURCE_BODY", json.dumps(context))
        self.assertEqual([r["task_id"] for r in supplementary], [f"work-{i}" for i in range(2, 8)])
        row = supplementary[-1]
        self.assertEqual(row["result_id"], "result-work-7")
        self.assertEqual(row["kind"], "work")
        self.assertEqual(row["attempt"], "attempt-work-7")
        self.assertEqual(row["created"], 9)
        self.assertEqual(row["body"]["kind"], "review")
        self.assertEqual(row["body"]["summary"], "AUTHOR_SELF_APPROVAL")

    def test_old_review_and_required_context_survive_newer_implementation_results(self):
        context = self.context()
        self.assertEqual(len(context["prior_reviews"]), 1)
        row = context["prior_reviews"][0]
        self.assertEqual(row["task_id"], "review-old")
        self.assertEqual(row["attempt"], "attempt-review-old")
        self.assertEqual(row["body"]["summary"], "EARLIER_REVIEW")
        self.assertNotIn("FOREIGN_TRACK", json.dumps(context))
        self.assertEqual(context["document"], self.document)
        self.assertEqual(context["document_revision"], 3)
        self.assertEqual(context["head"], "candidate")
        self.assertEqual(context["diff"], "CANDIDATE_DIFF")
        self.assertEqual(context["verification"], self.verification)
        self.assertEqual(context["verification_logs"], self.logs)
        self.assertEqual(context["evidence_artifacts"], self.artifacts)
        self.assertEqual(context["decisions"], self.snapshot["decisions"])
        self.assertEqual(context["findings"], self.snapshot["findings"][:1])
        self.assertEqual(context["incoming_findings"], [{"target": "example"}])
        self.assertIsNone(context["review"])

    def test_review_handoff_remains_path_only_and_preserves_separate_artifacts(self):
        context = self.context()
        folder = self.root / "fresh-attempt"
        folder.mkdir()
        data = worker_input(context, folder)
        self.assertEqual(data["task"]["attempt"], "fresh-attempt")
        self.assertNotIn("recent_results", data["paths"])
        for sentinel in (
            "AUTHOR_SELF_APPROVAL",
            "AUTHOR_SOURCE_BODY",
            "EARLIER_REVIEW",
            "CANDIDATE_DIFF",
        ):
            self.assertNotIn(sentinel, json.dumps(data))
        for key in (
            "document",
            "decisions",
            "findings",
            "incoming_findings",
            "verification",
            "verification_logs",
            "evidence_artifacts",
            "review",
            "prior_reviews",
            "supplementary_results",
        ):
            self.assertEqual(json.loads(Path(data["paths"][key]).read_text()), context[key])
        self.assertEqual(Path(data["paths"]["diff"]).read_text(), "CANDIDATE_DIFF")
        self.assertEqual(data["paths"]["track_document"], context["track_document"])

    def test_absent_evidence_remains_absent_despite_author_success_claims(self):
        self.track["verification"] = None
        self.logs = None
        self.artifacts = []
        context = self.context()
        self.assertIsNone(context["verification"])
        self.assertIsNone(context["review"])
        self.assertEqual(context["evidence_artifacts"], [])
        self.assertEqual(
            context["supplementary_results"][-1]["body"]["summary"], "AUTHOR_SELF_APPROVAL"
        )

    def test_nonreview_recent_results_contract_is_preserved(self):
        context = self.context("work")
        task_ids = {r["id"] for r in self.snapshot["tasks"] if r["track"] == "example"}
        expected = [
            json.loads(r["body"]) for r in self.snapshot["results"] if r["task"] in task_ids
        ][-6:]
        self.assertEqual(context["recent_results"], expected)
        self.assertNotIn("supplementary_results", context)
        self.assertNotIn("prior_reviews", context)
