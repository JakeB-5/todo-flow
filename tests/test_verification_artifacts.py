import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import verification_artifacts as artifacts
from todo_flow.adapters import command
from todo_flow.store import Conflict


GENERATED = (
    ".venv/pyvenv.cfg",
    ".venv/lib/site-packages/example.py",
    "dist/example.whl",
    ".ruff_cache/version/cache",
    "src/example.egg-info/PKG-INFO",
    "build/lib/example.py",
    "tests/__pycache__/example.pyc",
)


class VerificationArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command(["git", "init", "-q"], self.repo)
        (self.repo / ".gitignore").write_text(
            ".venv/\ndist/\nbuild/\n.ruff_cache/\n*.egg-info/\n__pycache__/\n",
            encoding="utf-8",
        )
        (self.repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
        command(["git", "add", "."], self.repo)
        command(
            [
                "git",
                "-c",
                "user.name=Artifact Test",
                "-c",
                "user.email=artifact@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            self.repo,
        )
        self.workspace = self.root / "candidate"
        command(["git", "worktree", "add", "-b", "candidate", str(self.workspace)], self.repo)
        self.state = self.root / "state"
        self.track = "artifact-test"
        self.execution = 0

    def capture(self, argv=None):
        self.execution += 1
        launch = {
            "directory": str(self.state),
            "track": self.track,
            "attempt": "attempt-test",
            "execution": f"verification-{self.execution}",
        }
        return artifacts.Capture(self.workspace, launch, argv or ["fixture-verifier"])

    def write(self, name, content="generated"):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def reclaim(self, **kwargs):
        return artifacts.reclaim(self.state, self.track, self.workspace, **kwargs)

    def test_local_verifier_artifacts_are_removed_and_branch_and_evidence_survive(self):
        outside = self.root / "outside"
        outside.write_text("keep external target", encoding="utf-8")
        code = (
            "from pathlib import Path\n"
            f"for name in {GENERATED!r}:\n"
            " p = Path(name)\n"
            " p.parent.mkdir(parents=True, exist_ok=True)\n"
            " p.write_text('generated', encoding='utf-8')\n"
            "p = Path('.venv/bin/python')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.symlink_to({str(outside)!r})\n"
        )
        argv = [sys.executable, "-c", code]
        capture = self.capture(argv)
        subprocess.run(argv, cwd=self.workspace, check=True)
        capture.finish()
        expected = sorted([*GENERATED, ".venv/bin/python"])
        self.assertEqual(self.reclaim(dry_run=True), expected)
        self.assertTrue(all((self.workspace / name).exists() for name in expected))
        self.assertEqual(self.reclaim(), expected)
        self.assertEqual(self.reclaim(), [])
        self.assertEqual(outside.read_text(), "keep external target")
        self.assertTrue((self.workspace / "tracked.txt").exists())
        head = command(["git", "rev-parse", "HEAD"], self.workspace)
        command(["git", "worktree", "remove", str(self.workspace)], self.repo)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(command(["git", "rev-parse", "candidate"], self.repo), head)
        record = json.loads(capture.path.read_text())
        self.assertEqual(record["phase"], "complete")
        self.assertEqual(record["command"], argv)
        self.assertEqual(sorted(record["files"]), expected)

    def test_preexisting_and_unrelated_files_block_all_reclamation(self):
        existing = self.write(".venv/operator.txt", "user environment")
        capture = self.capture()
        generated = self.write("dist/example.whl")
        unrelated = self.write(" notes\n한글.txt", "user notes")
        capture.finish()
        record = json.loads(capture.path.read_text())
        self.assertEqual(set(record["files"]), {"dist/example.whl"})
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        self.assertEqual(existing.read_text(), "user environment")
        self.assertEqual(unrelated.read_text(), "user notes")
        self.assertTrue(generated.exists())

    def test_external_edit_is_preserved_and_not_readopted_by_next_verification(self):
        first = self.capture()
        path = self.write(".venv/generated.txt")
        first.finish()
        path.write_text("user edit", encoding="utf-8")
        with self.assertRaisesRegex(Conflict, "changed user files"):
            self.reclaim()
        second = self.capture()
        path.write_text("generated", encoding="utf-8")
        second.finish()
        self.assertEqual(json.loads(second.path.read_text())["files"], {})
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        self.assertTrue(path.exists())

    def test_confirmed_failed_run_then_retry_keeps_attribution(self):
        code = (
            "from pathlib import Path; "
            "p=Path('.venv/generated.txt'); p.parent.mkdir(exist_ok=True); "
            "p.write_text('generated'); raise SystemExit(1)"
        )
        argv = [sys.executable, "-c", code]
        first = self.capture(argv)
        self.assertEqual(subprocess.run(argv, cwd=self.workspace).returncode, 1)
        first.finish()
        second = self.capture()
        self.write(".venv/generated.txt", "regenerated")
        second.finish()
        self.assertEqual(self.reclaim(), [".venv/generated.txt"])

    def test_unfinished_capture_preserves_files_and_cannot_finish_after_replacement(self):
        first = self.capture()
        path = self.write("dist/example.whl")
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        second = self.capture()
        with self.assertRaisesRegex(Conflict, "superseded"):
            first.finish()
        second.finish()
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        self.assertTrue(path.exists())

    def test_partial_deletion_can_resume_without_discarding_evidence(self):
        capture = self.capture()
        self.write("dist/a.whl")
        self.write("dist/b.whl")
        capture.finish()
        unlink = Path.unlink

        def interrupt_after_unlink(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            raise RuntimeError("lost response after unlink")

        with patch.object(Path, "unlink", interrupt_after_unlink):
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                self.reclaim()
        self.assertFalse((self.workspace / "dist/a.whl").exists())
        self.assertTrue((self.workspace / "dist/b.whl").exists())
        self.assertEqual(self.reclaim(), ["dist/b.whl"])
        self.assertEqual(len(json.loads(capture.path.read_text())["files"]), 2)

    def test_changed_symlink_ancestor_preserves_external_target(self):
        capture = self.capture()
        self.write(".venv/generated.txt")
        capture.finish()
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "generated.txt"
        target.write_text("external user file", encoding="utf-8")
        (self.workspace / ".venv").rename(self.root / "old-environment")
        (self.workspace / ".venv").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        self.assertEqual(target.read_text(), "external user file")
        self.assertTrue((self.workspace / ".venv").is_symlink())

    def test_recreated_checkout_at_same_path_cannot_use_old_evidence(self):
        capture = self.capture()
        self.write("dist/example.whl")
        capture.finish()
        old = self.root / "old-checkout"
        self.workspace.rename(old)
        self.workspace.mkdir()
        for name in (".git", ".gitignore", "tracked.txt"):
            (self.workspace / name).write_bytes((old / name).read_bytes())
        replacement = self.write("dist/example.whl")
        self.assertNotEqual(artifacts.checkout_identity(self.workspace), capture.identity)
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        self.assertTrue(replacement.exists())
        self.assertTrue((old / "dist/example.whl").exists())

    def test_missing_or_mismatched_evidence_never_authorizes_deletion(self):
        path = self.write("dist/example.whl", "user file")
        with self.assertRaisesRegex(Conflict, "Unattributed"):
            self.reclaim()
        path.unlink()
        capture = self.capture()
        path = self.write("dist/example.whl")
        capture.finish()
        record = json.loads(capture.path.read_text())
        record["track"] = "different-owner"
        capture.path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(Conflict, "does not match"):
            self.reclaim()
        self.assertTrue(path.exists())
