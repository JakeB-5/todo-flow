import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from todo_flow import skill_updates
from todo_flow.cli import main


class SkillCoexistenceCliTests(unittest.TestCase):
    def test_alias_install_and_update_preserve_foreign_context_and_infer_owned_state(self):
        for agent in (".agents", ".claude"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / "bundle"
                role = source / "todo"
                role.mkdir(parents=True)
                original = "---\nname: todo\ndescription: Register work\n---\nUse todo.\n"
                (role / "SKILL.md").write_text(original)
                target = root / "project" / agent / "skills"
                foreign = target / "todo"
                foreign.mkdir(parents=True)
                (foreign / "SKILL.md").write_text("Foreign workflow\n")
                (foreign / "project.json").write_text(
                    json.dumps({"state": str(root / "foreign-state"), "language": "ko"})
                )
                before = skill_updates.signature(foreign)
                state = root / "flow-state"
                with (
                    patch.dict(os.environ, {"TODO_FLOW_HOME": str(root / "control")}),
                    patch.object(skill_updates, "bundle", return_value=source),
                ):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        main(
                            [
                                "--state",
                                str(state),
                                "install-skills",
                                "--target",
                                str(target),
                                "--language",
                                "en",
                                "--alias",
                                "todo=flow-todo",
                            ]
                        )
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["entrypoints"], {"todo": "flow-todo"})
                    self.assertEqual(report["state"], str(state))
                    installed = target / "flow-todo"
                    self.assertIn("name: flow-todo\n", (installed / "SKILL.md").read_text())
                    self.assertEqual(
                        json.loads((installed / "project.json").read_text())["state"], str(state)
                    )
                    self.assertEqual(skill_updates.signature(foreign), before)
                    (role / "SKILL.md").write_text(original + "Updated instructions.\n")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        main(["update-skills", "--target", str(target)])
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["state"], str(state))
                    self.assertEqual(report["language"], "en")
                    self.assertEqual(report["entrypoints"], {"todo": "flow-todo"})
                    self.assertIn("Updated instructions.", (installed / "SKILL.md").read_text())
                    self.assertEqual(skill_updates.signature(foreign), before)

    def test_alias_for_unoccupied_role_is_rejected_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "bundle"
            role = source / "todo"
            role.mkdir(parents=True)
            (role / "SKILL.md").write_text("---\nname: todo\n---\nRegister work.\n")
            target = root / "project/.claude/skills"
            errors = io.StringIO()
            with (
                patch.dict(os.environ, {"TODO_FLOW_HOME": str(root / "control")}),
                patch.object(skill_updates, "bundle", return_value=source),
                redirect_stderr(errors),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(
                    [
                        "--state",
                        str(root / "state"),
                        "install-skills",
                        "--target",
                        str(target),
                        "--language",
                        "en",
                        "--alias",
                        "todo=flow-todo",
                    ]
                )
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn("occupied, unowned canonical entrypoint", errors.getvalue())
            self.assertFalse(target.exists())
