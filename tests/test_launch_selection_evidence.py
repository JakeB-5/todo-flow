"""Route evidence must survive both fallback and failures before process start."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.launchers import LauncherUnavailable
from todo_flow.worker import run_worker


class LaunchSelectionEvidenceTests(unittest.TestCase):
    def invoke(self, root, mode):
        return run_worker(
            {
                "worker_protocol": 2,
                "worker_launcher": mode,
                "worker": {"type": "command", "argv": ["unused-fixture-worker"]},
            },
            {"workspace": str(root)},
            {"attempt": "selection", "kind": "work"},
            root,
            lambda _: None,
        )

    def receipt(self, root):
        return json.loads((root / "attempts/selection/launch.json").read_text())

    def test_headless_reasons_survive_before_process_start(self):
        for mode, reason in (("headless", "explicit_headless"), ("auto", "cli_missing")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def fail_start(*args, **kwargs):
                    record = self.receipt(root)
                    self.assertEqual(record["backend"], "headless")
                    self.assertEqual(record["status"], "selected")
                    self.assertEqual(record["selection"]["requested"], mode)
                    self.assertEqual(record["selection"]["backend"], "headless")
                    self.assertEqual(record["selection"]["reason"], reason)
                    self.assertFalse(record["selection"]["native_ready"])
                    raise RuntimeError("fixture start failed")

                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch("todo_flow.launchers.shutil.which", return_value=None),
                    patch("todo_flow.launchers.probe_native_contract") as probe,
                    patch("todo_flow.worker.SupervisedProcess", side_effect=fail_start),
                    patch("todo_flow.worker.spawn_terminal") as terminal,
                ):
                    with self.assertRaisesRegex(RuntimeError, "fixture start failed"):
                        self.invoke(root, mode)
                    probe.assert_not_called()
                    terminal.assert_not_called()
                self.assertEqual(self.receipt(root)["selection"]["reason"], reason)

    def test_explicit_orca_failure_is_recorded_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("todo_flow.launchers.shutil.which", return_value=None),
                patch("todo_flow.worker.SupervisedProcess") as process,
                patch("todo_flow.worker.spawn_terminal") as terminal,
            ):
                with self.assertRaises(LauncherUnavailable) as raised:
                    self.invoke(root, "orca")
                process.assert_not_called()
                terminal.assert_not_called()
            record = self.receipt(root)
            self.assertIsNone(record["backend"])
            self.assertEqual(record["status"], "unavailable")
            self.assertEqual(record["selection"], raised.exception.selection)
            self.assertEqual(record["selection"]["reason"], "cli_missing")
            self.assertEqual(record["selection"]["requested"], "orca")

    def test_remote_host_evidence_survives_fallback_and_explicit_failure(self):
        for mode in ("auto", "orca"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                probe = {
                    "status": "native_contract_unverified",
                    "native_ready": False,
                    "schema_sha256": "fixture-schema-digest",
                }
                responses = [
                    {"app": {"running": True}, "runtime": {"reachable": True}},
                    {"worktree": {"id": "repo::/remote/path", "hostId": "runtime:remote"}},
                ]
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch("todo_flow.launchers.shutil.which", return_value="/fixture/orca"),
                    patch("todo_flow.launchers.probe_native_contract", return_value=probe),
                    patch("todo_flow.launchers.orca_result", side_effect=responses),
                    patch(
                        "todo_flow.worker.SupervisedProcess",
                        side_effect=RuntimeError("fixture start failed"),
                    ) as process,
                    patch("todo_flow.worker.spawn_terminal") as terminal,
                ):
                    with self.assertRaises(RuntimeError):
                        self.invoke(root, mode)
                    self.assertEqual(process.call_count, int(mode == "auto"))
                    terminal.assert_not_called()
                record = self.receipt(root)
                self.assertEqual(record["selection"]["reason"], "remote_host_mismatch")
                self.assertEqual(record["selection"]["host"], "runtime:remote")
                self.assertEqual(record["selection"]["orca"], probe)
                self.assertEqual(record["backend"], "headless" if mode == "auto" else None)
                self.assertEqual(
                    record["status"], "selected" if mode == "auto" else "unavailable"
                )

    def test_evidence_write_failure_prevents_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = Path.write_text

            def write(path, content, *args, **kwargs):
                if path.name == "launch.json":
                    raise OSError("fixture evidence storage unavailable")
                return original(path, content, *args, **kwargs)

            with (
                patch.object(Path, "write_text", write),
                patch("todo_flow.worker.SupervisedProcess") as process,
                patch("todo_flow.worker.spawn_terminal") as terminal,
            ):
                with self.assertRaisesRegex(OSError, "fixture evidence storage unavailable"):
                    self.invoke(root, "headless")
                process.assert_not_called()
                terminal.assert_not_called()
