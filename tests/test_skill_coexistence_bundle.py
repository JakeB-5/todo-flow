"""Filesystem installation evidence; no Claude process or model is invoked."""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from todo_flow import skill_updates
from todo_flow.cli import main
from todo_flow.file_store import atomic


ROLES = {
    "todo",
    "track-picks",
    "trackrun",
    "track-run",
    "watchlist",
    "track-work",
    "track-review",
    "track-land",
    "track-triage",
}
ALIASES = {role: "flow-" + role for role in ("todo", "track-picks", "track-run", "watchlist")}
AGENTS = (".agents", ".claude")


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            ("link", os.readlink(path))
            if path.is_symlink()
            else ("file", path.read_bytes())
            if path.is_file()
            else ("directory",)
        )
        for path in sorted(root.rglob("*"))
    }


class SkillCoexistenceBundleTests(unittest.TestCase):
    @contextmanager
    def fixture(self, agent, occupied=True):
        with tempfile.TemporaryDirectory() as directory:
            self.root = Path(directory).resolve()
            self.project = self.root / "project"
            self.source = self.root / "bundle"
            # Dereference the real template links, just as payload installation does.
            shutil.copytree(Path(__file__).resolve().parents[1] / "skills", self.source)
            self.original = skill_updates.payloads(self.source)
            self.assertEqual(set(self.original), ROLES)
            self.target = self.project / agent / "skills"
            self.target.mkdir(parents=True)
            self.state = self.project / "flow-state"
            foreign_state = self.project / "todo"
            foreign_state.mkdir()
            (foreign_state / "user-data.txt").write_text("Other workflow state\n")
            external = self.project / "other-workflow"
            external.mkdir()
            (external / "SKILL.md").write_text(
                "---\nname: track-run\ndescription: Other workflow\n---\nKeep me.\n"
            )
            if occupied:
                foreign = self.target / "todo"
                foreign.mkdir()
                (foreign / "SKILL.md").write_text(
                    "---\nname: todo\ndescription: Other workflow\n---\nKeep me.\n"
                )
                (foreign / "project.json").write_text(
                    json.dumps({"state": str(foreign_state), "language": "ko"})
                )
                (self.target / "track-picks").write_text("Other workflow file\n")
                (self.target / "track-run").symlink_to(external, target_is_directory=True)
                (self.target / "watchlist").symlink_to(external / "missing")
            self.foreign = snapshot(self.project)
            with (
                patch.dict(os.environ, {"TODO_FLOW_HOME": str(self.root / "control")}),
                patch.object(skill_updates, "bundle", return_value=self.source),
            ):
                yield

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            main(list(args))
        return json.loads(output.getvalue())

    def rejected(self, *args):
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            self.cli(*args)
        self.assertEqual(stopped.exception.code, 2)
        return json.loads(error.getvalue())["error"]

    def install(self, aliases=None):
        args = [
            "--state",
            str(self.state),
            "install-skills",
            "--target",
            str(self.target),
            "--language",
            "en",
        ]
        for role, name in (aliases or {}).items():
            args.extend(["--alias", f"{role}={name}"])
        return self.cli(*args)

    def update(self, *args):
        # Exercise CLI context inference from owned manifests, not foreign project.json.
        return self.cli("update-skills", "--target", str(self.target), *args)

    def assert_foreign_preserved(self):
        current = snapshot(self.project)
        for path, value in self.foreign.items():
            self.assertEqual(current[path], value, path)

    def assert_installation(self, report, aliases=None):
        mapping = {role: (aliases or {}).get(role, role) for role in ROLES}
        self.assertEqual(report["entrypoints"], mapping)
        self.assertEqual(report["state"], str(self.state))
        self.assertEqual(report["language"], "en")
        discovered = {}
        for path in self.target.glob("*/SKILL.md"):
            match = re.search(r"^name: (.+)$", path.read_text(), re.MULTILINE)
            self.assertIsNotNone(match, str(path))
            discovered[match[1]] = path
        for role, name in mapping.items():
            folder = self.target / name
            self.assertEqual(discovered[name], folder / "SKILL.md")
            text = (folder / "SKILL.md").read_text()
            frontmatter = text.split("---", 2)
            self.assertEqual(frontmatter[0], "")
            self.assertIn(f"\nname: {name}\n", frontmatter[1])
            self.assertRegex(frontmatter[1], r"\ndescription: .+")
            self.assertIn("project.json", text)
            self.assertEqual(
                json.loads((folder / "project.json").read_text()),
                {"state": str(self.state), "language": "en"},
            )
            metadata = json.loads((folder / skill_updates.MANIFEST).read_text())
            self.assertEqual(metadata["format"], 2 if aliases else 1)
            if aliases:
                self.assertEqual(metadata["entrypoints"], mapping)
                for canonical, entry in mapping.items():
                    self.assertIn(f"- {canonical} → [{entry}](../{entry}/SKILL.md)", text)
            else:
                self.assertNotIn("entrypoints", metadata)
                self.assertEqual(
                    (folder / "SKILL.md").read_bytes(), self.original[role]["SKILL.md"]
                )
            for relative, data in self.original[role].items():
                installed = (folder / relative).read_bytes()
                if relative != "SKILL.md":
                    self.assertEqual(installed, data, f"{name}/{relative}")
                self.assertEqual(metadata["files"][relative], skill_updates.digest(installed))
            for relative in re.findall(r"\]\(([^)]+)\)", text):
                self.assertTrue((folder / relative).is_file(), f"{name}: {relative}")
            original_text = self.original[role]["SKILL.md"].decode()
            # Shell fences and inline executable examples must remain byte-identical.
            for code in re.findall(r"```[\s\S]*?```|`[^`\n]+`", original_text):
                command = code.strip("`")
                if code.startswith("```") or command.startswith(
                    ("todo-flow", "trackrun", "uv run")
                ):
                    self.assertIn(code, text)
        for asset in ("track.html", "track.md"):
            self.assertEqual(
                (self.target / mapping["todo"] / "assets" / asset).read_bytes(),
                (Path(__file__).resolve().parents[1] / "templates" / asset).read_bytes(),
            )
        if aliases:
            self.assertIn("todo", discovered)  # The other workflow remains discoverable.
            todo = (self.target / mapping["todo"] / "SKILL.md").read_text()
            self.assertIn("`flow-track-picks`", todo)
            self.assertIn("`trackrun ID…`", todo)
            compatibility = (self.target / mapping["track-run"] / "SKILL.md").read_text()
            self.assertIn("[trackrun](../trackrun/SKILL.md)", compatibility)
        self.assert_foreign_preserved()

    def test_real_bundle_keeps_all_names_without_collisions(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent, occupied=False):
                self.assert_installation(self.install())
                before = snapshot(self.project)
                self.assertTrue(self.update()["unchanged"])
                self.assertEqual(snapshot(self.project), before)

    def test_four_collisions_preserve_files_links_and_resolve_all_roles(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                self.assert_installation(self.install(ALIASES), ALIASES)
                before = snapshot(self.project)
                self.assertTrue(self.update()["unchanged"])
                self.assertEqual(snapshot(self.project), before)

    def test_occupied_alias_aborts_entire_installation(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                (self.target / "flow-watchlist").symlink_to(self.project / "missing-alias")
                before = snapshot(self.project)
                args = [
                    "--state",
                    str(self.state),
                    "install-skills",
                    "--target",
                    str(self.target),
                    "--language",
                    "en",
                ]
                for role, name in ALIASES.items():
                    args.extend(["--alias", f"{role}={name}"])
                error = self.rejected(*args)
                self.assertIn("watchlist: entrypoint flow-watchlist is occupied", error)
                self.assertIn("--alias watchlist=NAME or a separate --target", error)
                self.assertEqual(snapshot(self.project), before)
                self.assertFalse(skill_updates.pending_path(self.target).exists())

    def change_upstream(self):
        for role in ("todo", "track-triage"):
            path = self.source / role / "SKILL.md"
            path.write_bytes(path.read_bytes() + b"\nUpdated vendor instructions.\n")

    def test_alias_update_preserves_local_edits_baseline_and_rolls_back_exactly(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                self.install(ALIASES)
                local = self.target / "flow-todo"
                asset = local / "assets/track.md"
                asset.write_bytes(asset.read_bytes() + b"\nLocal template notes.\n")
                (local / "custom.md").write_text("User instructions\n")
                before = snapshot(self.project)
                self.change_upstream()
                plan = self.update("--dry-run")
                self.assertIn("flow-todo/assets/track.md", plan["preserved"])
                self.assertEqual(snapshot(self.project), before)
                report = self.update()
                self.assertEqual(report["entrypoints"], {r: ALIASES.get(r, r) for r in ROLES})
                self.assertEqual(report["state"], str(self.state))
                self.assertEqual(report["language"], "en")
                self.assertIn("Updated vendor instructions.", (local / "SKILL.md").read_text())
                self.assertTrue(asset.read_bytes().endswith(b"Local template notes.\n"))
                self.assertEqual((local / "custom.md").read_text(), "User instructions\n")
                metadata = json.loads((local / skill_updates.MANIFEST).read_text())
                self.assertEqual(metadata["entrypoints"], report["entrypoints"])
                self.assertEqual(
                    metadata["files"]["assets/track.md"],
                    skill_updates.digest(self.original["todo"]["assets/track.md"]),
                )
                self.assertEqual(
                    json.loads((local / "project.json").read_text()),
                    {"state": str(self.state), "language": "en"},
                )
                self.assert_foreign_preserved()
                later = local / "later.md"
                later.write_text("Created after update\n")
                edited = snapshot(self.project)
                error = self.rejected(
                    "update-skills",
                    "--target",
                    str(self.target),
                    "--rollback",
                    report["backup"],
                )
                self.assertIn("changed after the update", error)
                self.assertEqual(snapshot(self.project), edited)
                later.unlink()
                self.update("--rollback", report["backup"])
                self.assertEqual(snapshot(self.project), before)
                _, mapping = skill_updates.installations(self.target)
                self.assertEqual(mapping, report["entrypoints"])

    def test_alias_update_conflict_leaves_every_role_and_foreign_workflow_unchanged(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                self.install(ALIASES)
                local = self.target / "flow-todo/SKILL.md"
                local.write_bytes(local.read_bytes() + b"\nLocal instructions.\n")
                self.change_upstream()
                before = snapshot(self.project)
                plan = self.update("--dry-run")
                self.assertTrue(any("flow-todo/SKILL.md" in c for c in plan["conflicts"]))
                error = self.rejected("update-skills", "--target", str(self.target))
                self.assertIn("nothing changed", error)
                self.assertEqual(snapshot(self.project), before)
                self.assertFalse(skill_updates.pending_path(self.target).exists())

    def test_partial_alias_update_and_interrupted_restore_recover_using_saved_context(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                report = self.install(ALIASES)
                before = snapshot(self.project)
                self.change_upstream()
                calls = 0

                def stop_after_writes(path, data):
                    nonlocal calls
                    calls += 1
                    if calls == 4:
                        raise OSError("Interrupted write")
                    return atomic(path, data)

                with (
                    patch("todo_flow.file_store.atomic", side_effect=stop_after_writes),
                    patch.object(
                        skill_updates, "restore_snapshot", side_effect=OSError("Restore stopped")
                    ),
                ):
                    error = self.rejected("update-skills", "--target", str(self.target))
                self.assertIn("Restore stopped", error)
                self.assertEqual(calls, 4)
                self.assertNotEqual(snapshot(self.project), before)
                self.assertTrue(skill_updates.pending_path(self.target).exists())
                self.assertEqual(
                    skill_updates.project_contexts(self.target, recover=True),
                    [{"state": str(self.state), "language": "en"}],
                )
                self.assert_foreign_preserved()
                self.update("--recover")
                self.assertFalse(skill_updates.pending_path(self.target).exists())
                self.assertEqual(snapshot(self.project), before)
                _, mapping = skill_updates.installations(self.target)
                self.assertEqual(mapping, report["entrypoints"])

    def test_unknown_alias_manifest_format_is_rejected_before_writes(self):
        for agent in AGENTS:
            with self.subTest(agent=agent), self.fixture(agent):
                self.install(ALIASES)
                path = self.target / "flow-todo" / skill_updates.MANIFEST
                metadata = json.loads(path.read_text())
                metadata["format"] = 999
                path.write_text(json.dumps(metadata))
                before = snapshot(self.project)
                error = self.rejected("update-skills", "--target", str(self.target))
                self.assertIn("Unsupported skill installation format/protocol: flow-todo", error)
                self.assertEqual(snapshot(self.project), before)
