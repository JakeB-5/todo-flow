import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import terminal_worker
from todo_flow.launchers import LauncherUnavailable, select_launcher, spawn_terminal
from todo_flow.orca_capabilities import probe_native_contract
from todo_flow.worker import run_worker


class LauncherTests(unittest.TestCase):
    def orca_selection(self, *, host="local", discovery="native_contract_unverified", mode="auto"):
        report = {"status": discovery, "native_ready": False, "advertised": {}}
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("todo_flow.launchers.orca_command", return_value="selected-orca"),
            patch("todo_flow.launchers.shutil.which", return_value="/bin/orca"),
            patch("todo_flow.launchers.probe_native_contract", return_value=report) as probe,
            patch(
                "todo_flow.launchers.orca_result",
                side_effect=[
                    {"app": {"running": True}, "runtime": {"reachable": True}},
                    {"worktree": {"id": "example::/project", "hostId": host}},
                ],
            ) as lookup,
        ):
            result = select_launcher({"worker_launcher": mode}, "/project")
        probe.assert_called_once_with("selected-orca", str(Path("/project").resolve()))
        self.assertEqual(lookup.call_count, 2)
        return result

    def test_auto_prefers_reachable_orca_project(self):
        result = self.orca_selection()
        self.assertEqual(result["backend"], "orca")
        self.assertEqual(result["worktree"], "id:example::/project")
        self.assertEqual(result["selection"]["backend"], "orca")
        self.assertEqual(result["selection"]["reason"], "native_contract_unverified")
        self.assertFalse(result["selection"]["native_ready"])

    def test_old_discovery_remains_distinct_on_legacy_terminal_route(self):
        result = self.orca_selection(discovery="discovery_command_unsupported")
        self.assertEqual(result["backend"], "orca")
        self.assertEqual(result["selection"]["reason"], "discovery_command_unsupported")
        self.assertFalse(result["selection"]["native_ready"])

    def test_remote_and_unknown_hosts_do_not_select_orca(self):
        for host, reason in (("remote-1", "remote_host_mismatch"), (None, "host_unverified")):
            with self.subTest(host=host):
                result = self.orca_selection(host=host)
                self.assertEqual(result["backend"], "headless")
                self.assertEqual(result["selection"]["backend"], "headless")
                self.assertEqual(result["selection"]["reason"], reason)
                self.assertEqual(result["selection"]["host"], host)
                with self.assertRaises(LauncherUnavailable) as caught:
                    self.orca_selection(host=host, mode="orca")
                self.assertEqual(caught.exception.selection["reason"], reason)
                self.assertIsNone(caught.exception.selection["backend"])

    def test_auto_without_terminal_uses_headless_and_explicit_orca_fails(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("todo_flow.launchers.shutil.which", return_value=None),
            patch("todo_flow.launchers.probe_native_contract") as probe,
        ):
            result = select_launcher({}, "/project")
            self.assertEqual(result["backend"], "headless")
            self.assertEqual(result["selection"]["reason"], "cli_missing")
            with self.assertRaisesRegex(LauncherUnavailable, "Orca terminal unavailable") as caught:
                select_launcher({"worker_launcher": "orca"}, "/project")
            self.assertEqual(caught.exception.selection["reason"], "cli_missing")
            probe.assert_not_called()

    def test_headless_override_does_not_probe_terminal(self):
        with (
            patch("todo_flow.launchers.orca_result") as lookup,
            patch("todo_flow.launchers.probe_native_contract") as probe,
            patch("todo_flow.launchers.shutil.which") as which,
        ):
            result = select_launcher({"worker_launcher": "headless"}, "/project")
            self.assertEqual(result["backend"], "headless")
            self.assertEqual(result["selection"]["reason"], "explicit_headless")
            self.assertEqual(result["selection"]["orca"]["status"], "not_probed")
            lookup.assert_not_called()
            probe.assert_not_called()
            which.assert_not_called()

    def test_tmux_is_used_inside_existing_session(self):
        with (
            patch.dict(os.environ, {"TMUX": "/tmp/tmux-example"}),
            patch(
                "todo_flow.launchers.shutil.which",
                side_effect=lambda cmd: "/bin/tmux" if cmd == "tmux" else None,
            ),
        ):
            result = select_launcher({}, "/project")
            self.assertEqual(result["backend"], "tmux")
            self.assertEqual(result["selection"]["backend"], "tmux")
            self.assertEqual(result["selection"]["reason"], "cli_missing")

    def test_public_custom_argv_is_evidence_not_native_authorization(self):
        commands = []
        for name, flags in (
            ("terminal create", ["worktree", "command"]),
            ("orchestration worker-start", ["worktree", "terminal", "retry-request"]),
        ):
            commands.append({"command": name, "path": name.split(), "flags": flags})
        completed = subprocess.CompletedProcess(
            [], 0, json.dumps({"schemaVersion": 1, "commands": commands}), ""
        )
        with patch("todo_flow.orca_capabilities.subprocess.run", return_value=completed):
            report = probe_native_contract("selected-orca", "/project")
        self.assertTrue(report["advertised"]["custom_terminal_command"])
        self.assertTrue(report["advertised"]["existing_terminal_worker"])
        policy = report["documented_custom_argv"]
        self.assertEqual(
            policy["policy_args"], ["--sandbox", "read-only", "--ask-for-approval", "never"]
        )
        self.assertEqual(policy["agent_first_exception"], "sandbox_approval_argv_unavailable")
        self.assertFalse(policy["runtime_verified"])
        self.assertFalse(report["native_ready"])

    def test_orca_selection_is_written_before_terminal_creation(self):
        launcher = self.orca_selection()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)

            def create(cli, args, cwd):
                record = json.loads((folder / "launch.json").read_text())
                self.assertEqual(record["status"], "launching")
                self.assertEqual(record["selection"], launcher["selection"])
                self.assertEqual(args[:2], ["terminal", "create"])
                self.assertIn("id:example::/project", args)
                return {"terminal": {"handle": "fixture-terminal"}}

            with patch("todo_flow.launchers.orca_result", side_effect=create) as launch:
                spawn_terminal(launcher, [sys.executable, "-c", "pass"], tmp, folder, "fixture")
            launch.assert_called_once()
            record = json.loads((folder / "launch.json").read_text())
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["selection"], launcher["selection"])
            self.assertFalse(record["selection"]["native_ready"])

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
            record = json.loads((folder / "launch.json").read_text())
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["selection"]["backend"], "terminal")
            self.assertEqual(record["selection"]["reason"], "explicit_launcher")
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
                "selection": {
                    "backend": "orca",
                    "reason": "native_contract_unverified",
                    "native_ready": False,
                },
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
            record = json.loads((folder / "launch.json").read_text())
            self.assertEqual(record["status"], "unconfirmed")
            self.assertEqual(record["selection"], launcher["selection"])
            self.assertTrue((folder / "terminal-cancelled").exists())
            late = subprocess.run(
                [sys.executable, terminal_worker.__file__, str(folder / "terminal-spec.json")]
            )
            self.assertEqual(late.returncode, 1)
            self.assertFalse((folder / "terminal-process.json").exists())
