import errno
import json
import os
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.checkout import require_clean
from todo_flow.engine import Engine
from todo_flow.store import Conflict
from todo_flow.supervised_process import SupervisedProcess
from todo_flow.worker import codex_workspace_tmpdir, run_worker


class FakeStore:
    def __init__(self, path, track):
        self.path = path
        self.track_record = track

    @contextmanager
    def transaction(self):
        yield None

    def assert_claim(self, _connection, _task):
        return None

    def track(self, _track_id):
        return self.track_record


class WorkerTmpdirTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="worker-tmpdir-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "worktree"
        self.workspace.mkdir()
        self.state = self.root / "state"

    def init_git(self, workspace=None, *, tracked_tmp=False):
        workspace = workspace or self.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "checkout", "-q", "-b", "todo/track-1-request"], cwd=workspace, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=workspace, check=True
        )
        (workspace / "tracked.txt").write_text("clean source\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=workspace, check=True)
        if tracked_tmp:
            tracked = workspace / ".tmp" / "tracked.txt"
            tracked.parent.mkdir()
            tracked.write_text("tracked source\n")
            subprocess.run(["git", "add", ".tmp/tracked.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "test fixture"], cwd=workspace, check=True)
        return "todo/track-1-request"

    def ensure_through_engine(self, workspace, state, attempt="attempt-next"):
        branch = "todo/track-1-request"
        track = {
            "branch": branch,
            "workspace": str(workspace),
            "request": "request-1",
            "head": None,
        }
        engine = Engine.__new__(Engine)
        engine.store = FakeStore(state, track)
        engine.config = {"repo": str(workspace), "base": "main"}
        engine.root = workspace
        engine.update = lambda _task, **values: track.update(values)
        task = {"track": "track-1", "attempt": attempt}
        with (
            patch("todo_flow.engine.proposal_application.read", return_value=None),
            patch("todo_flow.engine.managed_workspace.ensure", return_value=None),
        ):
            result = engine.ensure_workspace(task)
        return result, track

    def test_worker_scratch_write_stays_outside_worktree_and_checkout_is_clean(self):
        self.init_git()
        environment = os.environ.copy()
        environment["TODO_FLOW_CODEX_SANDBOX"] = "workspace-write"
        observed = {}

        def spawn(args, **kwargs):
            observed["tmpdir"] = Path(kwargs["env"]["TMPDIR"])
            observed["cwd"] = Path(kwargs["cwd"])
            (observed["tmpdir"] / "xcrun_db").write_text("scratch")
            Path(args[args.index("--output-last-message") + 1]).write_text(
                json.dumps({"summary": "Created scratch outside the worktree"})
            )
            return SupervisedProcess([sys.executable, "-c", "pass"], **kwargs)

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("todo_flow.worker.SupervisedProcess", spawn),
        ):
            result = run_worker(
                {
                    "worker": {"type": "codex"},
                    "worker_launcher": "headless",
                    "codex_sandbox": "workspace-write",
                },
                {"workspace": str(self.workspace)},
                {"attempt": "attempt-first", "kind": "assess"},
                self.state,
                lambda _pid: None,
            )

        expected = self.state / "attempts" / "attempt-first" / "tmp"
        self.assertEqual(result["summary"], "Created scratch outside the worktree")
        self.assertEqual(observed["tmpdir"], expected.resolve())
        self.assertEqual(observed["cwd"], self.workspace.resolve())
        self.assertTrue((expected / "xcrun_db").is_file())
        self.assertFalse((self.workspace / ".tmp").exists())
        self.assertTrue(require_clean(self.workspace))

    def test_native_workspace_write_receives_the_same_attempt_tmpdir(self):
        environment = os.environ.copy()
        environment["TODO_FLOW_CODEX_SANDBOX"] = "workspace-write"
        observed = {}

        def run_native(
            _config, _context, _task, _state, _folder, _launcher, _heartbeat, _observation, **kwargs
        ):
            observed["tmpdir"] = kwargs["tmpdir"]
            return {"summary": "native scratch path accepted"}

        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "todo_flow.worker.select_launcher",
                return_value={"backend": "orca", "cli": "orca"},
            ),
            patch("todo_flow.native_worker.run_native", side_effect=run_native),
        ):
            result = run_worker(
                {
                    "worker": {"type": "codex"},
                    "worker_launcher": "orca",
                    "codex_sandbox": "workspace-write",
                },
                {"workspace": str(self.workspace)},
                {"attempt": "attempt-native", "kind": "assess"},
                self.state,
                lambda _pid: None,
            )

        expected = self.state / "attempts/attempt-native/tmp"
        self.assertEqual(result["summary"], "native scratch path accepted")
        self.assertEqual(Path(observed["tmpdir"]), expected.resolve())
        self.assertFalse((self.workspace / ".tmp").exists())

    def test_protocol_two_command_adapter_receives_attempt_tmpdir(self):
        self.init_git()
        adapter = self.root / "adapter.py"
        adapter.write_text(
            "import json,os\nfrom pathlib import Path\n"
            "tmp=Path(os.environ['TMPDIR'])\n"
            "(tmp/'xcrun_db').write_text('scratch')\n"
            "print(json.dumps({'summary':str(tmp)}))\n"
        )
        environment = os.environ.copy()
        environment["TODO_FLOW_CODEX_SANDBOX"] = "workspace-write"
        attempt = "attempt-command-adapter"

        with patch.dict(os.environ, environment, clear=True):
            result = run_worker(
                {
                    "worker_protocol": 2,
                    "worker_launcher": "headless",
                    "worker": {"type": "command", "argv": [sys.executable, str(adapter)]},
                    "codex_sandbox": "workspace-write",
                },
                {"workspace": str(self.workspace)},
                {"attempt": attempt, "kind": "work"},
                self.state,
                lambda _pid: None,
            )

        expected = (self.state / "attempts" / attempt / "tmp").resolve()
        self.assertEqual(Path(result["summary"]), expected)
        self.assertTrue((expected / "xcrun_db").is_file())
        self.assertFalse((self.workspace / ".tmp").exists())
        self.assertTrue(require_clean(self.workspace))

    def test_engine_moves_legacy_directory_before_next_checkout_check(self):
        self.init_git()
        legacy = self.workspace / ".tmp" / "nested" / "xcrun_db"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("preserve me")

        workspace, track = self.ensure_through_engine(self.workspace, self.state)

        preserved = list(
            (self.state / "attempts/attempt-next/preserved-workspace-tmp").glob("*/nested/xcrun_db")
        )
        self.assertEqual(workspace, self.workspace)
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_text(), "preserve me")
        self.assertFalse((self.workspace / ".tmp").exists())
        self.assertTrue(require_clean(self.workspace, track["head"]))

    def test_multirepo_migrates_scratch_from_primary_worker_workspace(self):
        primary = self.root / "primary-repo"
        secondary = self.root / "secondary-repo"
        self.init_git(primary)
        self.init_git(secondary)
        legacy = primary / ".tmp" / "xcrun_db"
        legacy.parent.mkdir()
        legacy.write_text("primary scratch")

        engine = Engine.__new__(Engine)
        engine.store = FakeStore(self.state, {})
        engine.config = {"repos": [{"name": "primary"}, {"name": "secondary"}]}
        engine.root = primary
        workspaces = {"primary": primary, "secondary": secondary}
        task = {"track": "track-1", "attempt": "attempt-multirepo"}
        with (
            patch("todo_flow.engine.repositories.multi", return_value=True),
            patch(
                "todo_flow.engine.repositories.specs",
                return_value=[{"name": "primary"}, {"name": "secondary"}],
            ),
            patch.object(engine, "_ensure_repository_workspaces", return_value=workspaces),
        ):
            result = engine.ensure_workspace(task)

        self.assertEqual(result, workspaces)
        preserved = list(
            (self.state / "attempts/attempt-multirepo/preserved-workspace-tmp").glob("*/xcrun_db")
        )
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_text(), "primary scratch")
        self.assertFalse((primary / ".tmp").exists())
        self.assertTrue(require_clean(primary))
        self.assertTrue(require_clean(secondary))

    def test_legacy_file_and_root_symlink_are_moved_without_following(self):
        self.init_git()
        legacy_file = self.workspace / ".tmp"
        legacy_file.write_text("legacy file")
        codex_workspace_tmpdir(self.workspace, self.state, "attempt-file", create=False)
        moved_file = list((self.state / "attempts/attempt-file/preserved-workspace-tmp").glob("*"))
        self.assertEqual(len(moved_file), 1)
        self.assertEqual(moved_file[0].read_text(), "legacy file")
        self.assertFalse(legacy_file.exists())
        self.assertTrue(require_clean(self.workspace))

        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("outside target")
        legacy_file.symlink_to(outside, target_is_directory=True)
        codex_workspace_tmpdir(self.workspace, self.state, "attempt-link", create=False)
        moved_link = list((self.state / "attempts/attempt-link/preserved-workspace-tmp").glob("*"))
        self.assertEqual(len(moved_link), 1)
        self.assertTrue(moved_link[0].is_symlink())
        self.assertEqual(moved_link[0].readlink(), outside)
        self.assertEqual((outside / "sentinel").read_text(), "outside target")
        self.assertTrue(require_clean(self.workspace))

    def test_cross_device_copy_preserves_nested_symlink_and_files(self):
        self.init_git()
        outside = self.root / "outside-target"
        outside.mkdir()
        (outside / "sentinel").write_text("do not copy target")
        legacy = self.workspace / ".tmp"
        (legacy / "nested").mkdir(parents=True)
        (legacy / "nested" / "xcrun_db").write_text("keep")
        (legacy / "nested" / "outside-link").symlink_to(outside, target_is_directory=True)
        real_rename = os.rename

        def force_cross_device(source, destination):
            if os.fspath(source) == str(legacy):
                raise OSError(errno.EXDEV, "cross-device move")
            return real_rename(source, destination)

        with patch("todo_flow.worker.os.rename", side_effect=force_cross_device):
            codex_workspace_tmpdir(self.workspace, self.state, "attempt-cross-device", create=False)

        archive = self.state / "attempts/attempt-cross-device/preserved-workspace-tmp"
        moved = list(archive.iterdir())
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0] / "nested/xcrun_db").read_text(), "keep")
        self.assertTrue((moved[0] / "nested/outside-link").is_symlink())
        self.assertEqual((moved[0] / "nested/outside-link").readlink(), outside)
        self.assertEqual((outside / "sentinel").read_text(), "do not copy target")
        self.assertFalse(legacy.exists())
        self.assertTrue(require_clean(self.workspace))

    def test_refuses_tracked_or_non_git_legacy_tmp(self):
        tracked_root = self.root / "tracked-repo"
        self.init_git(tracked_root, tracked_tmp=True)
        tracked = tracked_root / ".tmp/tracked.txt"
        with self.assertRaisesRegex(Conflict, "contains tracked files"):
            codex_workspace_tmpdir(tracked_root, self.state, "attempt-tracked", create=False)
        self.assertEqual(tracked.read_text(), "tracked source\n")

        non_git = self.root / "non-git"
        non_git.mkdir()
        legacy = non_git / ".tmp"
        legacy.write_text("keep")
        with self.assertRaisesRegex(Conflict, "outside a Git worktree"):
            codex_workspace_tmpdir(non_git, self.state, "attempt-non-git", create=False)
        self.assertEqual(legacy.read_text(), "keep")

    def test_legacy_move_failure_keeps_original_path_and_blocks(self):
        self.init_git()
        legacy = self.workspace / ".tmp" / "xcrun_db"
        legacy.parent.mkdir()
        legacy.write_text("keep")
        with (
            patch("todo_flow.worker.shutil.move", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(Conflict, "checkout remains blocked"),
        ):
            codex_workspace_tmpdir(self.workspace, self.state, "attempt-move-fail", create=False)
        self.assertEqual(legacy.read_text(), "keep")

    def test_refuses_state_equal_or_nested_in_workspace_before_writing(self):
        for state in (self.workspace, self.workspace / "state/nested"):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "outside"):
                codex_workspace_tmpdir(self.workspace, state, "attempt-unsafe")
            self.assertFalse((self.workspace / "state").exists())

    def test_refuses_state_symlink_into_workspace(self):
        target = self.workspace / "linked-state"
        link = self.root / "state-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside"):
            codex_workspace_tmpdir(self.workspace, link, "attempt-link-state")
        self.assertFalse(target.exists())

    def test_rejects_unsafe_attempt_ids_and_mixed_types(self):
        invalid = [
            "",
            "../escape",
            "attempt/child",
            "attempt\\child",
            "attempt\u200b",
            "attempt-한글",
            None,
            True,
            0,
            [],
        ]
        for index, attempt in enumerate(invalid):
            with self.subTest(case=f"C{index + 1}", attempt=repr(attempt)):
                with self.assertRaisesRegex(ValueError, "safe path component"):
                    codex_workspace_tmpdir(self.workspace, self.state, attempt)
        self.assertFalse(self.state.exists())

    def test_rejects_symlinked_attempt_parents_and_archive(self):
        outside = self.root / "outside"
        outside.mkdir()

        state_for_attempts = self.root / "state-attempts-link"
        state_for_attempts.mkdir()
        (state_for_attempts / "attempts").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directories"):
            codex_workspace_tmpdir(self.workspace, state_for_attempts, "attempt")

        state_for_attempt = self.root / "state-attempt-link"
        (state_for_attempt / "attempts").mkdir(parents=True)
        (state_for_attempt / "attempts/attempt").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directories"):
            codex_workspace_tmpdir(self.workspace, state_for_attempt, "attempt")

        self.init_git()
        legacy = self.workspace / ".tmp"
        legacy.mkdir()
        archive = self.state / "attempts/attempt/preserved-workspace-tmp"
        archive.parent.mkdir(parents=True)
        archive.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            codex_workspace_tmpdir(self.workspace, self.state, "attempt", create=False)
        self.assertTrue(legacy.is_dir())

    def test_rejects_symlink_file_and_public_tmpdirs(self):
        outside = self.root / "outside"
        outside.mkdir()
        for attempt, kind in (
            ("attempt-link", "link"),
            ("attempt-file", "file"),
            ("attempt-mode", "mode"),
        ):
            tmpdir = self.state / "attempts" / attempt / "tmp"
            tmpdir.parent.mkdir(parents=True)
            if kind == "link":
                tmpdir.symlink_to(outside, target_is_directory=True)
            elif kind == "file":
                tmpdir.write_text("not a directory")
            else:
                tmpdir.mkdir(mode=0o755)
                tmpdir.chmod(0o755)
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(ValueError, "private real directory"),
            ):
                codex_workspace_tmpdir(self.workspace, self.state, attempt)

    def test_new_tmpdir_has_private_mode_and_permission_failures_propagate(self):
        path = codex_workspace_tmpdir(self.workspace, self.state, "attempt-private")
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)

        denied_state = self.root / "denied-state"
        original_mkdir = Path.mkdir

        def deny_tmpdir(path, *args, **kwargs):
            if path.name == "tmp":
                raise PermissionError("denied")
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", deny_tmpdir):
            with self.assertRaisesRegex(PermissionError, "denied"):
                codex_workspace_tmpdir(self.workspace, denied_state, "attempt-denied")

    def test_unicode_case_and_workspace_alias_preserve_resolved_path(self):
        workspace = self.root / "Repo Space 한글"
        workspace.mkdir()
        state = self.root / "State Area 한글"
        path = codex_workspace_tmpdir(workspace, state, "attempt-case-sensitive")
        self.assertEqual(path, (state / "attempts/attempt-case-sensitive/tmp").resolve())
        self.assertIn("Repo Space 한글", str(workspace.resolve()))
        self.assertIn("State Area 한글", str(path))

        alias = self.root / "Repo Alias"
        alias.symlink_to(workspace, target_is_directory=True)
        alias_tmp = codex_workspace_tmpdir(alias, state, "attempt-alias")
        self.assertEqual(alias_tmp, (state / "attempts/attempt-alias/tmp").resolve())

    def test_attempts_and_generations_never_share_tmpdir(self):
        first = codex_workspace_tmpdir(self.workspace, self.state, "attempt-generation-1")
        (first / "xcrun_db").write_text("first")
        second = codex_workspace_tmpdir(self.workspace, self.state, "attempt-generation-2")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "xcrun_db").read_text(), "first")
        self.assertFalse((second / "xcrun_db").exists())

    def test_concurrent_same_attempt_creates_one_stable_tmpdir(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            paths = list(
                pool.map(
                    lambda _: codex_workspace_tmpdir(
                        self.workspace, self.state, "attempt-parallel"
                    ),
                    range(24),
                )
            )
        expected = (self.state / "attempts/attempt-parallel/tmp").resolve()
        self.assertEqual(set(paths), {expected})
        self.assertTrue(expected.is_dir())

    def test_no_legacy_tmp_means_engine_does_not_create_preservation_state(self):
        result = codex_workspace_tmpdir(
            self.workspace, self.root / "unused-state", "attempt-noop", create=False
        )
        self.assertIsNone(result)
        self.assertFalse((self.root / "unused-state").exists())

    def test_moving_legacy_tmp_does_not_hide_unrelated_dirty_files(self):
        self.init_git()
        legacy = self.workspace / ".tmp" / "xcrun_db"
        legacy.parent.mkdir()
        legacy.write_text("preserve")
        unrelated = self.workspace / "user-change.txt"
        unrelated.write_text("do not move or hide")

        self.ensure_through_engine(self.workspace, self.state)

        self.assertTrue(unrelated.is_file())
        self.assertEqual(unrelated.read_text(), "do not move or hide")
        self.assertFalse((self.workspace / ".tmp").exists())
        with self.assertRaisesRegex(Conflict, "Checkout has existing changes"):
            require_clean(self.workspace)

    def test_invalid_workspace_types_and_missing_paths_fail_closed(self):
        missing = self.root / "missing"
        with self.assertRaises(FileNotFoundError):
            codex_workspace_tmpdir(missing, self.state, "attempt-missing")
        file_path = self.root / "workspace-file"
        file_path.write_text("not a directory")
        with self.assertRaisesRegex(ValueError, "must be a directory"):
            codex_workspace_tmpdir(file_path, self.state, "attempt-file-workspace")
        with self.assertRaises(TypeError):
            codex_workspace_tmpdir(None, self.state, "attempt-none-workspace")


if __name__ == "__main__":
    unittest.main()
