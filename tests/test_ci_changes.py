"""Release-only changes skip the suite; runtime and dependency changes do not."""

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "ci_changes", Path(__file__).resolve().parents[1] / "scripts/ci_changes.py"
)
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


class CIChangesTests(unittest.TestCase):
    def test_version_only_release_does_not_repeat_runtime_tests(self):
        changes = [
            (
                "pyproject.toml",
                '[project]\nname="todo-flow"\nversion="0.0.5"',
                '[project]\nname="todo-flow"\nversion="0.0.6"',
            ),
            (
                "uv.lock",
                '[[package]]\nname="todo-flow"\nversion="0.0.5"\nsource={editable="."}',
                '[[package]]\nname="todo-flow"\nversion="0.0.6"\nsource={editable="."}',
            ),
            ("src/todo_flow/release.py", '    VERSION = "0.0.5"\n', '    VERSION = "0.0.6"\n'),
            ("CHANGELOG.md", "previous", "release notes"),
        ]
        self.assertEqual(CI.classify(changes), (False, False))

    def test_dependency_and_build_changes_still_run_tests_and_update_smoke(self):
        for path, before, after in [
            (
                "pyproject.toml",
                '[project]\nversion="1.0.0"\ndependencies=[]',
                '[project]\nversion="1.0.1"\ndependencies=["new-dependency"]',
            ),
            (
                "uv.lock",
                '[[package]]\nname="dependency"\nversion="1"',
                '[[package]]\nname="dependency"\nversion="2"',
            ),
            (
                "src/todo_flow/release.py",
                'VERSION = "1.0.0"\ncheck()',
                'VERSION = "1.0.1"\nskip_check()',
            ),
        ]:
            with self.subTest(path=path):
                self.assertEqual(CI.classify([(path, before, after)]), (True, True))

    def test_mixed_runtime_changes_and_file_deletions_run_tests(self):
        self.assertEqual(
            CI.classify(
                [
                    ("README.md", "old", "new"),
                    ("src/todo_flow/terminal_slots.py", "removed implementation", None),
                ]
            ),
            (True, False),
        )
        self.assertEqual(CI.classify([("tests/test_worker.py", None, "new test")]), (True, False))

    def test_skills_and_updater_changes_run_update_checks(self):
        for path in ("skills/trackrun/SKILL.md", "src/todo_flow/engine_updates.py"):
            with self.subTest(path=path):
                self.assertEqual(CI.classify([(path, "old", "new")]), (True, True))

    def test_documentation_only_and_unreadable_metadata(self):
        self.assertEqual(CI.classify([("README.ko.md", "old", "new")]), (False, False))
        self.assertEqual(
            CI.classify([("pyproject.toml", "invalid TOML", "also invalid")]), (True, True)
        )
