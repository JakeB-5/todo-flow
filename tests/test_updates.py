import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow import skill_updates
from todo_flow.file_store import FileDatabase, atomic, dump
from todo_flow.maintenance import home, lease, project_lock, runtime_guard
from todo_flow.release import VERSION, project_compatibility, release_number
from todo_flow.schema import SCHEMA
from todo_flow.store import Store


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"TODO_FLOW_HOME": str(self.root / "control")})
        self.env.start()
        self.source = self.root / "bundle"
        self.source.mkdir()
        self.skill = self.source / "todo"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("Original instructions\n")
        (self.skill / "notes.txt").write_text("Bundled notes\n")
        self.target = self.root / "project/.agents/skills"
        self.state = self.root / "state"
        self.store = Store(self.state)
        self.store.configure({"language": "ko", "github": None})

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def install(self):
        return skill_updates.update(self.target, self.state, "ko", install=True, source=self.source)

    def update(self, **kwargs):
        major, minor, patch = release_number(VERSION)
        return skill_updates.update(
            self.target,
            self.state,
            source=self.source,
            version=f"{major}.{minor}.{patch + 1}",
            **kwargs,
        )

    def test_update_preserves_context_custom_files_and_unmodified_vendor_local_edits(self):
        self.install()
        local = self.target / "todo"
        context = (local / "project.json").read_bytes()
        (local / "custom.md").write_text("User additions")
        (local / "notes.txt").write_text("User-maintained notes")
        (self.skill / "SKILL.md").write_text("New instructions\n")
        report = self.update(dry_run=True)
        self.assertIn("todo/notes.txt", report["preserved"])
        self.assertEqual((local / "SKILL.md").read_text(), "Original instructions\n")
        receipt = self.update()
        self.assertEqual((local / "SKILL.md").read_text(), "New instructions\n")
        self.assertEqual((local / "notes.txt").read_text(), "User-maintained notes")
        self.assertEqual((local / "custom.md").read_text(), "User additions")
        self.assertEqual((local / "project.json").read_bytes(), context)
        self.assertTrue((home() / "skill-updates" / receipt["backup"] / "receipt.json").is_file())
        self.assertTrue(self.update()["unchanged"])

    def test_conflicting_changes_abort_the_entire_update(self):
        self.install()
        local = self.target / "todo"
        (local / "SKILL.md").write_text("My instructions")
        (self.skill / "SKILL.md").write_text("Vendor instructions")
        (self.skill / "notes.txt").write_text("New vendor notes")
        before = skill_updates.signature(local)
        self.assertTrue(self.update(dry_run=True)["conflicts"])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.update()
        self.assertEqual(skill_updates.signature(local), before)

    def test_vendor_removals_and_additions_preserve_unmanaged_files(self):
        self.install()
        local = self.target / "todo"
        (local / "custom.txt").write_text("Mine")
        (self.skill / "notes.txt").unlink()
        (self.skill / "new.txt").write_text("New bundled resource")
        self.update()
        self.assertFalse((local / "notes.txt").exists())
        self.assertEqual((local / "new.txt").read_text(), "New bundled resource")
        self.assertEqual((local / "custom.txt").read_text(), "Mine")

    def test_retired_bundled_skill_loses_only_owned_files(self):
        retired = self.source / "retired-role"
        retired.mkdir()
        (retired / "SKILL.md").write_text("Old role")
        self.install()
        (self.target / "retired-role/custom.txt").write_text("Keep my note")
        import shutil

        shutil.rmtree(retired)
        result = self.update()
        self.assertEqual(result["retired"], ["retired-role"])
        self.assertFalse((self.target / "retired-role/SKILL.md").exists())
        self.assertEqual((self.target / "retired-role/custom.txt").read_text(), "Keep my note")

    def test_rollback_refuses_later_edits_and_restores_exact_before_image(self):
        self.install()
        local = self.target / "todo"
        before = skill_updates.signature(local)
        (self.skill / "SKILL.md").write_text("New instructions")
        result = self.update()
        (local / "custom.txt").write_text("Added after update")
        with self.assertRaisesRegex(ValueError, "changed after"):
            skill_updates.restore(self.target, self.state, result["backup"])
        (local / "custom.txt").unlink()
        skill_updates.restore(self.target, self.state, result["backup"])
        self.assertEqual(skill_updates.signature(local), before)

    def test_partial_update_failure_rolls_back_all_files(self):
        self.install()
        local = self.target / "todo"
        before = skill_updates.signature(local)
        (self.skill / "SKILL.md").write_text("New instructions")
        (self.skill / "notes.txt").write_text("New notes")
        count = 0

        def fail_second(path, data):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Simulated disk failure")
            return atomic(path, data)

        with patch("todo_flow.file_store.atomic", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.update()
        self.assertEqual(skill_updates.signature(local), before)
        self.assertFalse(skill_updates.pending_path(self.target).exists())

    def test_interrupted_rollback_can_be_recovered_in_a_new_call(self):
        self.install()
        before = skill_updates.signature(self.target / "todo")
        (self.skill / "SKILL.md").write_text("New instructions")
        with (
            patch("todo_flow.file_store.atomic", side_effect=OSError("Interrupted")),
            patch(
                "todo_flow.skill_updates.restore_snapshot", side_effect=OSError("Process stopped")
            ),
        ):
            with self.assertRaises(OSError):
                self.update()
        self.assertTrue(skill_updates.pending_path(self.target).exists())
        skill_updates.restore(self.target, self.state)
        self.assertEqual(skill_updates.signature(self.target / "todo"), before)
        self.assertFalse(skill_updates.pending_path(self.target).exists())

    def test_legacy_adoption_requires_an_exact_known_baseline(self):
        self.install()
        metadata = self.target / "todo" / skill_updates.MANIFEST
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "no installation manifest"):
            self.update()
        self.update(adopt=True)
        metadata.unlink()
        (self.target / "todo/SKILL.md").write_text("Unknown custom instructions")
        with self.assertRaisesRegex(ValueError, "baseline"):
            self.update(adopt=True)
        self.assertFalse(metadata.exists())

    def test_symlinked_managed_file_is_not_overwritten(self):
        self.install()
        outside = self.root / "outside.txt"
        outside.write_text("Protected")
        local = self.target / "todo/SKILL.md"
        local.unlink()
        local.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.update()
        self.assertEqual(outside.read_text(), "Protected")

    def test_runtime_and_maintenance_exclude_each_other_and_other_projects_remain_independent(self):
        with runtime_guard(self.state):
            with self.assertRaises(RuntimeError), lease(home() / "runtime.lock", exclusive=True):
                pass
            with self.assertRaises(RuntimeError):
                self.install()
            with lease(project_lock(self.root / "other-project"), exclusive=True):
                pass
        with lease(home() / "runtime.lock", exclusive=True):
            with self.assertRaises(RuntimeError), runtime_guard(self.state):
                pass
        with runtime_guard(self.state):
            pass

    def test_unresolved_running_work_blocks_updates_even_without_a_driver(self):
        self.store.register(
            {
                "id": "sample",
                "title": "Sample",
                "goal": "A result",
                "scope": "A file",
                "evidence": "A request",
                "conditions": [{"id": "ok", "text": "Works", "method": "test"}],
            }
        )
        self.store.start("sample")
        self.store.claim("abandoned-driver")
        with self.assertRaisesRegex(RuntimeError, "running work"):
            self.install()
        self.assertFalse(self.target.exists())

    def test_future_catalog_or_recovery_journal_is_rejected_before_data_writes(self):
        catalog = self.state / ".catalog.json"
        original = json.loads(catalog.read_text())
        catalog.write_text(dump({**original, "format": 99}))
        with self.assertRaisesRegex(ValueError, "state format"):
            Store(self.state)
        self.assertFalse(project_compatibility(self.state)["compatible"])
        catalog.write_text(dump(original))
        pending = self.state / ".pending.json"
        pending.write_text(
            dump({"catalog": {**original, "format": 99}, "writes": {"unexpected.txt": "bad"}})
        )
        with self.assertRaisesRegex(ValueError, "state format"):
            FileDatabase(self.state, SCHEMA)
        self.assertFalse((self.state / "unexpected.txt").exists())
        self.assertTrue(pending.exists())

    def test_engine_preflight_rejects_candidate_that_cannot_read_known_state(self):
        from todo_flow.engine_updates import check_projects
        from todo_flow.release import CONTRACTS

        with runtime_guard(self.state):
            pass
        before = (self.state / ".catalog.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            check_projects({**CONTRACTS, "state_formats": [2]}, "0.0.2")
        self.assertEqual((self.state / ".catalog.json").read_bytes(), before)

    def test_engine_pending_marker_blocks_normal_use_but_not_version_reporting(self):
        import contextlib
        import io
        from todo_flow.cli import main

        (home() / "engine-pending.json").write_text('{"id":"pending"}')
        with self.assertRaisesRegex(RuntimeError, "interrupted"), runtime_guard(self.state):
            pass
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_:
            main(["--version"])
        self.assertEqual(exit_.exception.code, 0)
        from todo_flow.release import VERSION

        self.assertIn(f"todo-flow {VERSION}", output.getvalue())

    def test_corrupt_wheel_fails_before_installation(self):
        from todo_flow.engine_updates import inspect_wheel

        wheel = self.root / "broken.whl"
        wheel.write_bytes(b"Incomplete download")
        with self.assertRaisesRegex(ValueError, "Invalid TODO Flow release wheel"):
            inspect_wheel(wheel)

    def test_future_config_and_worker_protocol_are_rejected(self):
        config = self.state / "config/1.json"
        for field in ("schema_version", "worker_protocol"):
            config.write_text(dump({"id": 1, "body": {field: 99}}))
            self.assertFalse(project_compatibility(self.state)["compatible"])
            with self.assertRaisesRegex(ValueError, "Unsupported project"):
                Store(self.state).config()
