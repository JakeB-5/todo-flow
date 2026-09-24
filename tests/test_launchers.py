import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import terminal_worker
from todo_flow.launchers import select_launcher, spawn_terminal
from todo_flow.worker import run_worker


class LauncherTests(unittest.TestCase):
    def test_auto_prefers_reachable_orca_project(self):
        with (
            patch("todo_flow.launchers.shutil.which", return_value="/bin/orca"),
            patch(
                "todo_flow.launchers.orca_result",
                side_effect=[
                    {"app": {"running": True}, "runtime": {"reachable": True}},
                    {"worktree": {"id": "example::/project", "hostId": "local"}},
                ],
            ),
        ):
            self.assertEqual(select_launcher({}, "/project")["backend"], "orca")

    def test_auto_without_terminal_uses_headless_and_explicit_orca_fails(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("todo_flow.launchers.shutil.which", return_value=None),
        ):
            self.assertEqual(select_launcher({}, "/project"), {"backend": "headless"})
            with self.assertRaisesRegex(RuntimeError, "Orca terminal unavailable"):
                select_launcher({"worker_launcher": "orca"}, "/project")

    def test_headless_override_does_not_probe_terminal(self):
        with patch("todo_flow.launchers.orca_result") as probe:
            self.assertEqual(
                select_launcher({"worker_launcher": "headless"}, "/project"),
                {"backend": "headless"},
            )
            probe.assert_not_called()

    def test_tmux_is_used_inside_existing_session(self):
        with (
            patch.dict(os.environ, {"TMUX": "/tmp/tmux-example"}),
            patch(
                "todo_flow.launchers.shutil.which",
                side_effect=lambda cmd: "/bin/tmux" if cmd == "tmux" else None,
            ),
        ):
            self.assertEqual(select_launcher({}, "/project")["backend"], "tmux")

    def terminal_config(self, root, worker):
        launcher = root / "fake-terminal.py"
        launcher.write_text(
            "import subprocess,sys,shlex\n"
            "subprocess.Popen(shlex.split(sys.argv[1]), stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL, start_new_session=True)\n"
        )
        return {
            "worker_protocol": 2,
            "worker_launcher": "terminal",
            "terminal_command": [sys.executable, str(launcher), "{command}"],
            "worker": {"type": "command", "argv": worker},
        }

    def test_terminal_exit_receipt_and_output_survive_launcher_exit(self):
        with tempfile.TemporaryDirectory(prefix="flow space'") as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "source.txt").write_text("read from actual workspace")
            config = self.terminal_config(
                root,
                [
                    sys.executable,
                    "-c",
                    "import json;from pathlib import Path;print(json.dumps({'summary':Path('source.txt').read_text()}))",
                ],
            )
            pids = []
            result = run_worker(
                config,
                {"workspace": str(workspace)},
                {"attempt": "terminal", "kind": "work"},
                root / "state",
                pids.append,
            )
            self.assertEqual(result["summary"], "read from actual workspace")
            folder = root / "state/attempts/terminal"
            receipt = json.loads((folder / "terminal-process.json").read_text())
            self.assertEqual(receipt["returncode"], 0)
            self.assertEqual(receipt["status"], "exited")
            self.assertEqual(json.loads((folder / "launch.json").read_text())["status"], "accepted")
            # Re-delivery must not launch the same attempt again.
            duplicate = subprocess.run(
                [sys.executable, terminal_worker.__file__, str(folder / "terminal-spec.json")]
            )
            self.assertEqual(duplicate.returncode, 1)
            self.assertEqual(json.loads((folder / "terminal-process.json").read_text()), receipt)

    def test_terminal_timeout_stops_the_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.terminal_config(
                root, [sys.executable, "-c", "import time;time.sleep(60)"]
            )
            config["worker_timeout"] = 1
            with self.assertRaises(TimeoutError):
                run_worker(
                    config,
                    {"workspace": tmp},
                    {"attempt": "timeout", "kind": "work"},
                    root / "state",
                    lambda _: None,
                )
            folder = root / "state/attempts/timeout"
            receipt = json.loads((folder / "terminal-process.json").read_text())
            self.assertEqual(receipt["status"], "exited")
            self.assertNotEqual(receipt["returncode"], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(receipt["pid"], 0)

    def test_ambiguous_launch_blocks_late_start_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            launcher = {
                "backend": "orca",
                "cli": "orca",
                "worktree": "id:example::/repo",
                "repo": tmp,
            }
            with patch(
                "todo_flow.launchers.orca_result", side_effect=subprocess.TimeoutExpired("orca", 10)
            ) as launch:
                with self.assertRaises(subprocess.TimeoutExpired):
                    spawn_terminal(
                        launcher,
                        [sys.executable, "-c", "raise Exception('must not run')"],
                        tmp,
                        folder,
                        "test",
                    )
                launch.assert_called_once()
            self.assertEqual(
                json.loads((folder / "launch.json").read_text())["status"], "unconfirmed"
            )
            self.assertTrue((folder / "terminal-cancelled").exists())
            late = subprocess.run(
                [sys.executable, terminal_worker.__file__, str(folder / "terminal-spec.json")]
            )
            self.assertEqual(late.returncode, 1)
            self.assertFalse((folder / "terminal-process.json").exists())
