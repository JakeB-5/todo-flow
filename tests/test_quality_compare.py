"""Exercise comparable cohorts, worker provenance, time/retry scope and offline replay."""

from contextlib import chdir, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import quality_compare


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = tuple(f"examples/quality-run-{name}.json" for name in "abcd")
FORBIDDEN = {"rank", "ranking", "success_rate", "score", "price"}


def collect_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(collect_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(collect_keys(item) for item in value))
    return set()


def observed(total, cases, missing):
    return {"observed_total": total, "observed_cases": cases, "unobserved_cases": missing}


def labels(cohort):
    return [(item["model"]["value"], item["model"]["basis"]) for item in cohort["workers"]]


class QualityComparisonTests(unittest.TestCase):
    def setUp(self):
        self.records = {}
        for name, path in zip("abcd", SAMPLES):
            record = json.loads((ROOT / path).read_text(encoding="utf-8"))
            inputs = record["inputs"]
            for field, value in inputs.items():
                inputs[field] = str(ROOT / "examples" / value)
            self.records[name] = record
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def path(self, name):
        return self.directory / f"{name}.json"

    def compare(self, *names):
        paths = []
        for name in names or "abcd":
            text = json.dumps(self.records[name], ensure_ascii=False)
            self.path(name).write_text(text, encoding="utf-8")
            paths.append(self.path(name))
        return quality_compare.compare(paths)

    def capture_main(self, argv):
        output = io.StringIO()
        with chdir(ROOT), redirect_stdout(output):
            status = quality_compare.main(argv)
        return status, output.getvalue()

    def test_sample_markdown_matches_and_reports_repeat_byte_for_byte(self):
        sample = (ROOT / "examples/quality-comparison-report.md").read_text(encoding="utf-8")
        markdown = ["--format", "markdown", *SAMPLES]
        self.assertEqual(self.capture_main(markdown), (0, sample))
        self.assertEqual(self.capture_main(markdown), (0, sample))
        first = self.capture_main(list(SAMPLES))
        self.assertEqual(first[0], 0)
        self.assertEqual(self.capture_main(list(SAMPLES)), first)

    def test_only_same_case_expectation_and_condition_form_a_cohort(self):
        report = self.compare()
        key = ["fixture_id", "cases_sha256", "expectations_sha256", "verification"]
        self.assertEqual(report["comparison_key"], key)
        [cohort] = report["cohorts"]
        self.assertEqual(cohort["key"]["expectations_id"], "quality-expectations-v1")
        self.assertEqual(cohort["samples"], 3)
        totals = {"cases": 18, "completed": 9, "failed": 3, "inconclusive": 6}
        self.assertEqual(cohort["denominator"], totals)
        confirmed, selected = cohort["workers"]
        self.assertEqual((confirmed["samples"], selected["samples"]), (2, 1))
        counts = {"completed": 6, "skipped": 2, "failed": 2, "missing_response": 2}
        self.assertEqual(confirmed["status_counts"], counts)
        denominator = {"cases": 12, "completed": 6, "failed": 2, "inconclusive": 4}
        self.assertEqual(confirmed["denominator"], denominator)
        denominator = {"cases": 6, "completed": 3, "failed": 1, "inconclusive": 2}
        self.assertEqual(selected["denominator"], denominator)
        definition = report["denominator_definition"]
        self.assertIn("모델 판단이 맞았다는 근거가 아닙니다", definition)
        self.assertEqual(confirmed["metrics"]["missed_defects"], observed(2, 6, 6))
        self.assertEqual(confirmed["metrics"]["execution_failures"], observed(2, 10, 2))
        [single] = report["non_comparable"]
        self.assertEqual(single["attempts"][0]["attempt"], "attempt-d")
        self.assertEqual(single["key"]["expectations_id"], "quality-expectations-v2")
        self.assertFalse(collect_keys(report) & FORBIDDEN)

    def test_changed_case_content_under_same_id_is_not_comparable(self):
        cases = json.loads((ROOT / "examples/quality-cases.json").read_text(encoding="utf-8"))
        copied = self.directory / "quality-cases-copy.json"
        copied.write_bytes((ROOT / "examples/quality-cases.json").read_bytes())
        self.records["c"]["inputs"]["cases"] = str(copied)
        [cohort] = self.compare("a", "c")["cohorts"]
        self.assertEqual(cohort["samples"], 2)
        cases["cases"]["clean"]["prompt"] += " 변경된 내용입니다."
        copied.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        report = self.compare("a", "c")
        self.assertEqual(report["cohorts"], [])
        keys = [item["key"] for item in report["non_comparable"]]
        self.assertEqual({key["fixture_id"] for key in keys}, {"quality-cases-v1"})
        self.assertEqual(len({key["cases_sha256"] for key in keys}), 2)
        for row in report["records"]:
            self.assertEqual(row["key"]["cases_sha256"], row["sources"]["cases"]["sha256"])

    def test_different_expectations_or_conditions_are_not_ranked_together(self):
        self.records["d"]["worker"] = copy.deepcopy(self.records["a"]["worker"])
        report = self.compare("a", "d")
        self.assertEqual(report["cohorts"], [])
        hashes = {item["key"]["expectations_sha256"] for item in report["non_comparable"]}
        self.assertEqual(len(hashes), 2)
        self.records["c"]["verification"] = "eval-replay"
        report = self.compare("a", "c")
        self.assertEqual(report["cohorts"], [])
        self.assertEqual(len(report["non_comparable"]), 2)

    def test_matching_key_joins_the_cohort_without_mixing_evidence_basis(self):
        expectations = str(ROOT / "examples/quality-expectations.json")
        self.records["d"]["inputs"]["expectations"] = expectations
        self.records["d"]["expectations"] = {"id": "quality-expectations-v1"}
        report = self.compare()
        self.assertEqual(report["non_comparable"], [])
        [cohort] = report["cohorts"]
        self.assertEqual(cohort["samples"], 4)
        expected = [
            ("sample-model-x", "confirmed"),
            ("sample-model-y", "confirmed"),
            ("sample-model-y", "selected"),
        ]
        self.assertEqual(labels(cohort), expected)

    def test_selected_only_record_is_not_reported_as_confirmed(self):
        report = self.compare()
        model = report["records"][1]["worker"]["model"]
        source = self.path("b").as_posix() + "#/worker/selected/model"
        selected = {"value": "sample-model-y", "status": "selected", "source": source}
        self.assertEqual(model["selected"], selected)
        unconfirmed = {"value": None, "status": "unconfirmed", "source": None}
        self.assertEqual(model["confirmed"], unconfirmed)
        worker = report["cohorts"][0]["workers"][1]
        self.assertEqual(worker["model"], {"value": "sample-model-y", "basis": "selected"})
        self.assertEqual(worker["effort"], {"value": "medium", "basis": "selected"})

    def test_selected_value_does_not_join_confirmed_group(self):
        self.records["e"] = copy.deepcopy(self.records["a"])
        self.records["e"]["attempt"] = "attempt-e"
        del self.records["e"]["worker"]["provider_confirmed"]
        [cohort] = self.compare("a", "e")["cohorts"]
        expected = [("sample-model-x", "confirmed"), ("sample-model-x", "selected")]
        self.assertEqual(labels(cohort), expected)
        confirmed = {"model": "sample-model-x", "effort": "high"}
        self.records["e"]["worker"]["provider_confirmed"] = confirmed
        [cohort] = self.compare("a", "e")["cohorts"]
        self.assertEqual(labels(cohort), [("sample-model-x", "confirmed")])
        self.assertEqual(cohort["workers"][0]["samples"], 2)

    def test_overlapping_attempts_are_not_summed_as_wall_clock_time(self):
        report = self.compare()
        confirmed, selected = report["cohorts"][0]["workers"]
        overlapping = {
            "attempt_seconds_total": 600,
            "observed_attempts": 2,
            "unobserved_attempts": 0,
            "wall_clock_seconds": 480,
        }
        self.assertEqual(confirmed["duration"], overlapping)
        unobserved = {
            "attempt_seconds_total": None,
            "observed_attempts": 0,
            "unobserved_attempts": 1,
            "wall_clock_seconds": None,
        }
        self.assertEqual(selected["duration"], unobserved)
        duration = report["records"][1]["duration"]
        self.assertEqual((duration["status"], duration["seconds"]), ("unobserved", None))
        self.assertIsNone(duration["finished_ref"])
        self.assertEqual(duration["started_ref"], self.path("b").as_posix() + "#/started_at")
        self.records["c"]["started_at"] = "2026-10-01T19:10:00+09:00"
        self.records["c"]["finished_at"] = "2026-10-01T19:15:00+09:00"
        confirmed = self.compare()["cohorts"][0]["workers"][0]
        self.assertEqual(confirmed["duration"]["attempt_seconds_total"], 600)
        self.assertEqual(confirmed["duration"]["wall_clock_seconds"], 600)

    def test_retries_count_only_observed_values(self):
        report = self.compare()
        retries = [row["retries"] for row in report["records"]]
        self.assertEqual(retries[0], observed(3, 4, 2))
        self.assertEqual(retries[1], observed(None, 0, 6))
        self.assertEqual(retries[2], observed(2, 6, 0))
        worker = report["cohorts"][0]["workers"][0]
        self.assertEqual(worker["retries"], observed(5, 10, 2))
        ref = self.path("a").as_posix() + "#/retries"
        self.assertEqual(report["records"][0]["retries_ref"], ref)

    def test_synthetic_replay_stays_synthetic_despite_record_metadata(self):
        self.records["a"]["synthetic"] = False
        self.records["c"]["synthetic"] = False
        report = self.compare("a", "c")
        self.assertTrue(report["synthetic"])
        for row in report["records"]:
            self.assertEqual((row["synthetic"], row["declared_synthetic"]), (True, False))
        self.assertIsNone(report["unmeasured"]["model_quality"])

    def test_inconsistent_or_malformed_records_are_rejected(self):
        changes = (
            ("expectations", {"id": "quality-expectations-v1", "sha256": "0" * 64}),
            ("fixture_id", "other-fixture"),
            ("verification", ""),
            ("synthetic", "true"),
            ("started_at", "2026-10-01T10:00:00"),
            ("finished_at", "2026-10-01T09:59:59Z"),
            ("retries", {"unknown": 1}),
            ("retries", {"clean": -1}),
            ("worker", {"selected": {"model": ""}}),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                original = copy.deepcopy(self.records["a"])
                self.records["a"][field] = value
                with self.assertRaises(ValueError):
                    self.compare("a")
                self.records["a"] = original
        self.records["c"]["attempt"] = "attempt-a"
        with self.assertRaises(ValueError):
            self.compare("a", "c")

    def test_command_runs_offline_and_preserves_input_hashes(self):
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError("network call")),
            patch("socket.socket", side_effect=AssertionError("socket call")),
            patch("socket.create_connection", side_effect=AssertionError("connection call")),
            patch("subprocess.Popen", side_effect=AssertionError("process call")),
            patch("os.system", side_effect=AssertionError("shell call")),
        ):
            status, output = self.capture_main(list(SAMPLES))
        self.assertEqual(status, 0)
        report = json.loads(output)
        self.assertTrue(report["synthetic"])
        unmeasured = {
            "model_quality": None,
            "human_review_seconds": None,
            "tokens": None,
            "cost": None,
        }
        self.assertEqual(report["unmeasured"], unmeasured)
        declared = self.records["a"]["expectations"]["sha256"]
        self.assertEqual(report["records"][0]["key"]["expectations_sha256"], declared)
        for row in report["records"]:
            for source in (row["record"], *row["sources"].values()):
                raw = (ROOT / source["path"]).read_bytes()
                self.assertEqual(source["sha256"], hashlib.sha256(raw).hexdigest())

    def test_command_requires_explicit_records(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            quality_compare.main([])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
