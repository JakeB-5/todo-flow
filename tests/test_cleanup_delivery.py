"""Exercise delivery authority, attributed residue and interrupted local cleanup."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from test_cleanup import CleanupTests
from todo_flow.adapters import command
from todo_flow.cleanup import cleanup_track, receipt_path
from todo_flow.cli import main
from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.store import encode


GENERATED = (
    ".venv/pyvenv.cfg",
    ".venv/lib/example.py",
    "dist/example.whl",
    ".ruff_cache/version/cache",
    "src/example.egg-info/PKG-INFO",
    "build/lib/example.py",
)


class CleanupDeliveryTests(unittest.TestCase):
    setUp = CleanupTests.setUp
    tearDown = CleanupTests.tearDown
    finish = CleanupTests.finish

    def configure(self, **changes):
        config = {**self.s.config(), **changes}
        with self.s.transaction() as connection:
            connection.execute("UPDATE config SET body=?", (encode(config),))

    def finish_generated(self):
        with (self.repo / ".git/info/exclude").open("a") as stream:
            stream.write("\n.venv/\ndist/\n.ruff_cache/\n*.egg-info/\nbuild/\n")
        original = self.s.config()["verify"]
        code = (
            "from pathlib import Path\n"
            "import subprocess\n"
            f"subprocess.run({original!r}, check=True)\n"
            f"for name in {GENERATED!r}:\n"
            "    path = Path(name)\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text('generated')\n"
        )
        self.configure(verify=[sys.executable, "-c", code])
        self.finish()
        self.track = self.s.track("addition")
        self.workspace = Path(self.track["workspace"])
        self.assertTrue(all((self.workspace / name).exists() for name in GENERATED))
        return self.workspace

    def candidate(self, report):
        workspace = self.s.track("addition")["workspace"]
        return next(item for item in report["worktrees"] if item["path"] == workspace)

    def assert_preserved(self, report):
        self.assertEqual(report["status"], "deferred", encode(report))
        self.assertEqual(self.candidate(report)["status"], "preserved", encode(report))
        self.assertTrue(Path(self.s.track("addition")["workspace"]).exists())
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_real_verifier_residue_is_reclaimed_under_review_default(self):
        workspace = self.finish_generated()
        self.configure(endpoint="review", allow_land=False)
        with patch("todo_flow.cleanup.orca_result") as orca:
            planned = cleanup_track(self.s, "addition", dry_run=True)
            self.assertEqual(planned["status"], "complete", encode(planned))
            self.assertEqual(planned["landing"]["status"], "confirmed")
            self.assertEqual(self.candidate(planned)["status"], "would-remove")
            self.assertTrue(all((workspace / name).exists() for name in GENERATED))
            self.assertFalse(receipt_path(self.s, self.track).exists())
            report = cleanup_track(self.s, "addition")
            orca.assert_not_called()
        self.assertEqual(report["status"], "complete", encode(report))
        self.assertEqual(report["landing"]["status"], "confirmed")
        self.assertEqual(self.candidate(report)["status"], "removed")
        self.assertTrue(set(GENERATED).issubset(self.candidate(report)["artifacts"]))
        self.assertFalse(workspace.exists())
        self.assertEqual(
            command(["git", "rev-parse", self.track["branch"]], self.repo), self.track["head"]
        )
        command(["git", "cat-file", "-e", self.track["head"] + "^{commit}"], self.repo)
        self.assertTrue(list((self.s.path / "attempts").glob("*/output.json")))
        self.assertTrue(list((self.s.path / "verification-artifacts").glob("*.json")))
        self.assertEqual(json.loads(receipt_path(self.s, self.track).read_text()), report)
        self.assertEqual(cleanup_track(self.s, "addition")["status"], "complete")

    def test_changed_generated_file_preserves_all_candidate_artifacts(self):
        workspace = self.finish_generated()
        path = workspace / GENERATED[0]
        path.write_text("user environment")
        report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertEqual(report["landing"]["status"], "confirmed")
        self.assertIn("changed user files", self.candidate(report)["reason"])
        self.assertEqual(path.read_text(), "user environment")
        self.assertTrue(all((workspace / name).exists() for name in GENERATED))

    def test_tracked_user_change_prevents_even_proven_artifact_reclamation(self):
        workspace = self.finish_generated()
        path = workspace / "calc.py"
        path.write_text("# user work\n")
        report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertIn("Tracked user changes", self.candidate(report)["reason"])
        self.assertEqual(path.read_text(), "# user work\n")
        self.assertTrue(all((workspace / name).exists() for name in GENERATED))

    def test_unattributed_bytecode_is_not_disposable_by_name(self):
        workspace = self.finish_generated()
        path = workspace / "__pycache__/operator.pyc"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"user-owned cache")
        report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertIn("Unattributed", self.candidate(report)["reason"])
        self.assertEqual(path.read_bytes(), b"user-owned cache")
        self.assertTrue(all((workspace / name).exists() for name in GENERATED))

    def test_mismatched_landing_and_review_receipts_preserve_candidate(self):
        self.finish()
        track = self.s.track("addition")
        landing = json.loads(track["landing"])
        review = json.loads(track["review"])
        cases = (
            ("landing", {**landing, "head": "0" * 40}),
            ("landing", {**landing, "merged": landing["baseBefore"]}),
            ("landing", {**landing, "effectId": "missing-effect"}),
            ("review", {**review, "documentRevision": track["revision"] + 1}),
            ("review", {**review, "verdict": "unmet"}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                with self.s.transaction() as connection:
                    connection.execute(
                        f"UPDATE tracks SET {field}=? WHERE id='addition'", (encode(value),)
                    )
                report = cleanup_track(self.s, "addition", dry_run=True)
                self.assert_preserved(report)
                self.assertEqual(report["landing"]["status"], "unconfirmed")
                with self.s.transaction() as connection:
                    connection.execute(
                        f"UPDATE tracks SET {field}=? WHERE id='addition'", (track[field],)
                    )

    def test_current_cleared_triage_is_required(self):
        self.finish()
        with self.s.transaction() as connection:
            connection.execute("DELETE FROM triages WHERE track='addition'")
        report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertIn("triage", self.candidate(report)["reason"])

    def test_remote_confirmation_failure_preserves_landed_checkout(self):
        self.finish()
        original = command

        def unavailable(argv, *args, **kwargs):
            if argv[:2] == ["git", "fetch"]:
                raise OSError("remote unavailable")
            return original(argv, *args, **kwargs)

        with patch("todo_flow.cleanup.command", side_effect=unavailable):
            report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertIn("remote unavailable", self.candidate(report)["reason"])

    def test_process_barrier_prevents_cleanup_of_unconfirmed_execution(self):
        self.finish()
        ProcessBarrier(self.s.path, "addition").begin(
            "attempt-unconfirmed",
            "execution-unconfirmed",
            reason="Pending execution",
            evidence={"launch": "fixture"},
        )
        with self.assertRaises(ProcessBarrierError):
            cleanup_track(self.s, "addition")
        self.assertTrue(Path(self.s.track("addition")["workspace"]).exists())

    def test_interruption_after_git_removal_retains_intent_for_requery(self):
        self.finish()
        track = self.s.track("addition")
        workspace = track["workspace"]
        original = command

        def interrupt(argv, *args, **kwargs):
            result = original(argv, *args, **kwargs)
            if argv == ["git", "worktree", "remove", workspace]:
                raise KeyboardInterrupt("Host stopped before the removal receipt")
            return result

        with patch("todo_flow.cleanup.command", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                cleanup_track(self.s, "addition")
        self.assertFalse(Path(workspace).exists())
        pending = json.loads(receipt_path(self.s, track).read_text())
        target = self.candidate(pending)["target"]
        self.assertTrue(self.candidate(pending)["removalIntent"])
        report = cleanup_track(self.s, "addition")
        self.assertEqual(report["status"], "complete", encode(report))
        self.assertEqual(self.candidate(report)["status"], "removed")
        self.assertEqual(self.candidate(report)["target"], target)
        self.assertEqual(command(["git", "rev-parse", track["branch"]], self.repo), track["head"])

    def test_recreated_path_cannot_inherit_an_interrupted_removal_intent(self):
        self.finish()
        track = self.s.track("addition")
        workspace = Path(track["workspace"])
        original = command

        def unavailable(argv, *args, **kwargs):
            if argv == ["git", "worktree", "remove", str(workspace)]:
                raise OSError("Removal unavailable")
            return original(argv, *args, **kwargs)

        with patch("todo_flow.cleanup.command", side_effect=unavailable):
            first = cleanup_track(self.s, "addition")
        self.assert_preserved(first)
        target = self.candidate(first)["target"]
        retained = self.root / "retained-checkout"
        workspace.rename(retained)
        shutil.copytree(retained, workspace)
        report = cleanup_track(self.s, "addition")
        self.assert_preserved(report)
        self.assertIn("target identity", self.candidate(report)["reason"])
        self.assertEqual(self.candidate(report)["target"], target)
        self.assertTrue((retained / "calc.py").exists())
        self.assertTrue((workspace / "calc.py").exists())

    def test_cli_distinguishes_confirmed_landing_from_deferred_cleanup(self):
        workspace = self.finish_generated()
        notes = workspace / "notes.txt"
        notes.write_text("keep my notes")
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--state", str(self.s.path), "cleanup", "addition"])
        report = json.loads(output.getvalue())
        self.assert_preserved(report)
        self.assertEqual(report["landing"]["status"], "confirmed")
        self.assertIn("notes.txt", self.candidate(report)["reason"])
        self.assertEqual(notes.read_text(), "keep my notes")
        self.assertEqual(json.loads(receipt_path(self.s, self.track).read_text()), report)
