"""Exercise synthetic scoring, missing observations, replay, and effect boundaries."""

from contextlib import chdir, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import quality_eval


ROOT = Path(__file__).resolve().parents[1]
INPUT_NAMES = ("cases", "responses", "expectations")


class QualityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.documents = [
            json.loads((ROOT / f"examples/quality-{name}.json").read_text(encoding="utf-8"))
            for name in INPUT_NAMES
        ]
        self.fixture, self.responses, self.expectations = self.documents
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def write_inputs(self):
        paths = []
        for name, document in zip(INPUT_NAMES, self.documents):
            path = self.directory / f"{name}.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            paths.append(path)
        return paths

    def replay(self):
        return quality_eval.replay(*self.write_inputs())

    def capture_main(self, argv):
        output = io.StringIO()
        with chdir(ROOT), redirect_stdout(output):
            status = quality_eval.main(argv)
        return status, output.getvalue()

    def test_known_totals_and_distinct_outcomes(self):
        report = self.replay()
        self.assertTrue(report["matches_expected"])
        self.assertEqual(report["metrics"], self.expectations["expected_metrics"])
        self.assertEqual(
            {key: value["observed_total"] for key, value in report["metrics"].items()},
            {"missed_defects": 1, "false_approvals": 1, "rework": 2, "execution_failures": 1},
        )
        rows = {row["case_id"]: row for row in report["rows"]}
        self.assertEqual(rows["clean"]["metrics"]["missed_defects"], 0)
        self.assertEqual(rows["missed"]["metrics"]["missed_defects"], 1)
        self.assertEqual(rows["caught"]["metrics"]["rework"], 2)
        for case_id in ("not-run", "missing-response", "failed"):
            for metric in ("missed_defects", "false_approvals", "rework"):
                self.assertIsNone(rows[case_id]["metrics"][metric])
        self.assertEqual(rows["not-run"]["metrics"]["execution_failures"], 0)
        self.assertIsNone(rows["missing-response"]["metrics"]["execution_failures"])
        self.assertEqual(rows["failed"]["metrics"]["execution_failures"], 1)
        self.assertIsNone(rows["missing-response"]["response_ref"])

    def test_no_responses_leave_totals_unobserved(self):
        self.responses["responses"] = {}
        report = self.replay()
        for metric in report["metrics"].values():
            self.assertEqual(
                metric, {"observed_total": None, "observed_cases": 0, "unobserved_cases": 6}
            )
        self.assertEqual(report["status_counts"]["missing_response"], 6)
        self.assertFalse(report["matches_expected"])

    def test_observed_zero_differs_from_no_observation(self):
        self.responses["responses"] = {"clean": self.responses["responses"]["clean"]}
        report = self.replay()
        for metric in report["metrics"].values():
            self.assertEqual(
                metric, {"observed_total": 0, "observed_cases": 1, "unobserved_cases": 5}
            )

    def test_completed_response_does_not_default_missing_fields_to_zero(self):
        self.responses["responses"]["clean"] = {"status": "completed"}
        row = self.replay()["rows"][0]
        self.assertEqual(
            row["metrics"],
            {
                "missed_defects": None,
                "false_approvals": None,
                "rework": None,
                "execution_failures": 0,
            },
        )

    def test_skipped_payload_cannot_supply_quality_observations(self):
        self.responses["responses"]["not-run"].update(found_defects=[], approved=True, rework=5)
        row = self.replay()["rows"][3]
        self.assertEqual(row["status"], "skipped")
        self.assertIsNone(row["metrics"]["missed_defects"])
        self.assertIsNone(row["metrics"]["false_approvals"])
        self.assertIsNone(row["metrics"]["rework"])

    def test_approval_and_detection_are_separate_measures(self):
        self.responses["responses"]["caught"]["approved"] = True
        report = self.replay()
        self.assertEqual(report["metrics"]["false_approvals"]["observed_total"], 2)
        self.assertEqual(report["metrics"]["missed_defects"]["observed_total"], 1)
        self.assertFalse(report["matches_expected"])

    def test_changed_truth_changes_scoring_without_changing_responses(self):
        before = self.replay()
        self.expectations["cases"]["missed"]["defects"] = []
        after = self.replay()
        self.assertEqual(before["sources"]["responses"], after["sources"]["responses"])
        self.assertNotEqual(
            before["sources"]["expectations"]["sha256"],
            after["sources"]["expectations"]["sha256"],
        )
        self.assertEqual(after["metrics"]["missed_defects"]["observed_total"], 0)
        self.assertEqual(after["metrics"]["false_approvals"]["observed_total"], 0)
        self.assertFalse(after["matches_expected"])

    def test_invalid_order_and_fixture_binding_are_rejected(self):
        self.fixture["order"].append("clean")
        with self.assertRaises(ValueError):
            self.replay()
        self.fixture["order"].pop()
        self.responses["fixture_id"] = "different-fixture"
        with self.assertRaises(ValueError):
            self.replay()

    def test_malformed_observations_are_rejected(self):
        for field, value in (
            ("status", "unknown"),
            ("approved", "true"),
            ("rework", True),
            ("rework", -1),
            ("found_defects", ["duplicate", "duplicate"]),
        ):
            with self.subTest(field=field, value=value):
                original = self.responses["responses"]["clean"].copy()
                self.responses["responses"]["clean"][field] = value
                with self.assertRaises(ValueError):
                    self.replay()
                self.responses["responses"]["clean"] = original

    def test_command_replays_sample_bytes_and_preserves_sources(self):
        first_status, first = self.capture_main([])
        second_status, second = self.capture_main([])
        sample = (ROOT / "examples/quality-report.json").read_text(encoding="utf-8")
        self.assertEqual((first_status, second_status), (0, 0))
        self.assertEqual(first, second)
        self.assertEqual(first, sample)
        report = json.loads(first)
        self.assertEqual(report["case_order"], self.fixture["order"])
        for source in report["sources"].values():
            raw = (ROOT / source["path"]).read_bytes()
            self.assertEqual(source["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(source["id"], json.loads(raw)["id"])
        self.assertTrue(report["synthetic"])
        self.assertEqual(
            report["unmeasured"],
            {"model_quality": None, "human_review_seconds": None, "tokens": None, "cost": None},
        )

    def test_command_reports_expectation_mismatch_with_nonzero_exit(self):
        self.expectations["expected_metrics"]["missed_defects"]["observed_total"] = 99
        cases, responses, expectations = self.write_inputs()
        status, output = self.capture_main(
            [
                "--cases",
                str(cases),
                "--responses",
                str(responses),
                "--expectations",
                str(expectations),
            ]
        )
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(output)["matches_expected"])

    def test_default_command_needs_no_network_or_process_adapter(self):
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError("network call")),
            patch("socket.socket", side_effect=AssertionError("socket call")),
            patch("socket.create_connection", side_effect=AssertionError("connection call")),
            patch("subprocess.Popen", side_effect=AssertionError("process call")),
            patch("os.system", side_effect=AssertionError("shell call")),
        ):
            status, output = self.capture_main([])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["adapter"], "fixed-response")


if __name__ == "__main__":
    unittest.main()
