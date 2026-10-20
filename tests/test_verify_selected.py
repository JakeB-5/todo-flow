"""Selection must omit unjustified checks before launching any verifier process."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "verify_selected", Path(__file__).resolve().parents[1] / "scripts/verify_selected.py"
)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class SelectedVerificationTests(unittest.TestCase):
    def run_plan(self, checks=None, document=None, returncode=0):
        document = (
            document
            if document is not None
            else {
                "version": 1,
                "plans": {
                    "current-tree": {"reason": "Assess the changed selector", "checks": checks}
                },
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plans.json"
            path.write_text(json.dumps(document))
            output = io.StringIO()
            with (
                patch.object(VERIFY.subprocess, "check_output", return_value="current-tree\n"),
                patch.object(
                    VERIFY.subprocess, "run", return_value=Mock(returncode=returncode)
                ) as run,
                contextlib.redirect_stdout(output),
            ):
                code = VERIFY.main(["--plans", str(path)])
        result = json.loads(output.getvalue().splitlines()[-1].removesuffix(VERIFY.RESULT_SUFFIX))
        return code, run, result, output.getvalue()

    def needed(self):
        return {
            "name": "selection behavior",
            "decision": "necessary",
            "reason": "Detect accidental execution of omitted checks",
            "evidence_gap": "Text review alone does not establish the subprocess call boundary",
            "argv": ["python3", "-m", "unittest", "test_selector"],
        }

    def test_only_necessary_checks_execute(self):
        omitted = {
            "name": "Git integration",
            "decision": "unnecessary",
            "reason": "This change only selects commands; it does not change Git operations",
            "argv": ["must-not-run"],
        }
        code, run, result, output = self.run_plan([omitted, self.needed()])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], self.needed()["argv"])
        self.assertIn("SKIP Git integration", output)
        self.assertIn("1 checks executed; 1 omitted", result["reason"])

    def test_undetermined_checks_do_not_execute_or_become_passes(self):
        code, run, result, _ = self.run_plan(
            [
                {
                    "name": "whole suite",
                    "decision": "undetermined",
                    "reason": "No established connection to the change",
                    "argv": ["must-not-run"],
                }
            ]
        )
        run.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result["outcome"], "inconclusive")

    def test_explicit_document_review_can_require_no_executable_checks(self):
        code, run, result, _ = self.run_plan(
            [
                {
                    "name": "application tests",
                    "decision": "unnecessary",
                    "reason": "Wording-only edit reviewed directly; no product behavior changed",
                }
            ]
        )
        run.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("0 checks executed; 1 omitted", result["reason"])

    def test_missing_stale_and_invalid_assessments_have_no_full_suite_fallback(self):
        for document in (
            {},
            {"version": 1, "plans": {}},
            {"version": 2, "plans": {}},
            {"version": 1, "plans": {"old-tree": {"reason": "old", "checks": [self.needed()]}}},
        ):
            with self.subTest(document=document):
                code, run, result, _ = self.run_plan(document=document)
                run.assert_not_called()
                self.assertEqual((code, result["outcome"]), (2, "inconclusive"))

    def test_validate_whole_selection_before_starting_first_check(self):
        for invalid in ({**self.needed(), "evidence_gap": ""}, {**self.needed(), "argv": []}):
            with self.subTest(invalid=invalid):
                code, run, _, _ = self.run_plan([self.needed(), invalid])
                run.assert_not_called()
                self.assertEqual(code, 2)

    def test_nonzero_exit_does_not_trigger_more_checks_or_imply_a_product_defect(self):
        code, run, result, _ = self.run_plan([self.needed(), self.needed()], returncode=7)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((code, result["outcome"]), (2, "inconclusive"))
