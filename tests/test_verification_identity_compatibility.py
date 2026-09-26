import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow import release
from todo_flow.verification_identity import VerificationIdentityError, validate


class VerificationIdentityCompatibilityTests(unittest.TestCase):
    def test_legacy_and_declared_configuration_need_no_input_observation(self):
        release.check_config({})
        config = {
            "verify_identity": {
                "version": 1,
                "files": ["missing-runner.py"],
                "environment": ["VERIFY_TOKEN"],
                "nonce": "rotation-2",
            }
        }
        before = json.dumps(config, sort_keys=True)
        with patch("todo_flow.verification_identity._file_identity") as observe:
            release.check_config(config)
            declaration = validate(config)
        observe.assert_not_called()
        declaration["files"].append("another.py")
        self.assertEqual(json.dumps(config, sort_keys=True), before)

    def test_invalid_declarations_are_rejected_by_both_entry_points(self):
        declarations = [
            None,
            [],
            {},
            {"version": True},
            {"version": 999},
            {"version": 1, "future": []},
            {"version": 1, "files": "runner.py"},
            {"version": 1, "files": ["runner.py", "runner.py"]},
            {"version": 1, "files": ["bad\0path"]},
            {"version": 1, "environment": ["BAD-NAME"]},
            {"version": 1, "environment": ["TOKEN", "TOKEN"]},
            {"version": 1, "nonce": 2},
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                config = {"verify_identity": declaration}
                with self.assertRaises(ValueError):
                    release.check_config(config)
                with self.assertRaises(VerificationIdentityError):
                    validate(config)

    def test_compatibility_report_rejects_unknown_identity_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / ".catalog.json").write_text(json.dumps({"format": 1}))
            (root / "config/1.json").write_text(
                json.dumps({"id": 1, "body": {"verify_identity": {"version": 999}}})
            )
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            report = release.project_compatibility(root)
            self.assertFalse(report["compatible"])
            self.assertTrue(any("identity" in issue for issue in report["issues"]))
            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)

    def test_copied_release_contract_runs_without_installed_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(release.__file__)
            shutil.copy2(source, root / "release.py")
            shutil.copy2(source.with_name("release.json"), root / "release.json")
            script = (
                "import runpy, sys\n"
                "contract = runpy.run_path(sys.argv[1])\n"
                "check = contract['check_config']\n"
                "check({})\n"
                "check({'verify_identity': {'version': 1, 'files': ['missing.py']}})\n"
                "try:\n"
                "    check({'verify_identity': {'version': 999}})\n"
                "except ValueError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('Unsupported identity accepted')\n"
                "assert not any(name == 'todo_flow' or name.startswith('todo_flow.') "
                "for name in sys.modules)\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(root / "release.py")],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
