import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import terminal_worker
from todo_flow.launchers import TerminalProcess, spawn_terminal
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots


class TerminalDispatchGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "attempt"
        self.folder.mkdir()
        (self.folder / "input.json").write_text("{}")
        self.identity = {
            "directory": str(self.root),
            "track": "track",
            "attempt": "attempt",
            "execution": "execution",
        }
        self.marker = self.root / "unexpected-worker"
        self.argv = [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(self.marker),
        ]

    def dispatch(self, exitcode=0):
        # A real launcher accepts the command but leaves delivery to the test.
        # It must see a durable intent before accepting that command.
        script = (
            "import json,sys; from pathlib import Path; "
            "from todo_flow.process_launch import LaunchGate; "
            "spec=json.loads(Path(sys.argv[1]).read_text()); "
            "gate=LaunchGate(**spec['launch_identity']); "
            "assert gate._event()['state']=='intent'; "
            "Path(sys.argv[2]).write_text(sys.argv[3]); "
            "raise SystemExit(int(sys.argv[4]))"
        )
        launcher = {
            "backend": "terminal",
            "argv": [
                sys.executable,
                "-c",
                script,
                str(self.folder / "terminal-spec.json"),
                str(self.root / "delivery"),
                "{command}",
                str(exitcode),
            ],
        }
        return spawn_terminal(
            launcher,
            self.argv,
            str(self.root),
            self.folder,
            "delayed",
            launch_identity=self.identity,
        )

    def assert_late_delivery_rejected(self):
        # Remove the legacy marker to prove that the durable gate itself rejects
        # direct file execution, including after a new TerminalProcess instance.
        (self.folder / "terminal-cancelled").unlink()
        result = subprocess.run(
            [sys.executable, terminal_worker.__file__, str(self.folder / "terminal-spec.json")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        receipt = json.loads((self.folder / "terminal-process.json").read_text())
        self.assertTrue(receipt["cleanup_confirmed"])
        self.assertEqual(receipt["status"], "exited")
        gate = LaunchGate(**self.identity)
        gate.barrier.require_clear()
        self.assertEqual(gate._event()["evidence"]["outcome"], "not-spawned")

    def test_accepted_delayed_dispatch_is_durably_cancelled_after_reopen(self):
        self.dispatch()
        self.assertTrue((self.root / "delivery").exists())
        with self.assertRaises(ProcessBarrierError):
            LaunchGate(**self.identity).barrier.require_clear()
        process = TerminalProcess(self.folder)
        process.started -= 60
        process.stop()
        self.assert_late_delivery_rejected()

    def test_failed_dispatch_cancels_before_late_delivery(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.dispatch(exitcode=9)
        self.assertTrue((self.root / "delivery").exists())
        self.assert_late_delivery_rejected()

    def test_consumed_permit_without_receipt_cannot_be_declared_unspawned(self):
        self.dispatch()
        gate = LaunchGate(**self.identity)
        with gate.launching():
            pass
        with self.assertRaises(ProcessBarrierError):
            TerminalProcess(self.folder).stop()
        with self.assertRaises(ProcessBarrierError):
            LaunchGate(**self.identity).barrier.require_clear()
        self.assertEqual(gate._event()["state"], "running")

    def test_prepare_failure_prevents_dispatch(self):
        # Inject failure at prepare itself; an unattributed existing intent is
        # rejected earlier by capacity admission and cannot exercise this path.
        with (
            patch(
                "todo_flow.launchers.LaunchGate.prepare",
                side_effect=ProcessBarrierError("Synthetic prepare failure"),
            ) as prepare,
            patch("todo_flow.launchers.subprocess.run") as dispatch,
        ):
            with self.assertRaisesRegex(ProcessBarrierError, "Synthetic prepare failure"):
                self.dispatch()
            prepare.assert_called_once_with(**self.identity, backend="terminal")
            dispatch.assert_not_called()
        self.assertFalse((self.folder / "terminal-spec.json").exists())
        self.assertFalse((self.root / "delivery").exists())
        slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.assertEqual(slots.counts(slots.snapshot())["reserved"], 1)

    def test_existing_unattributed_intent_prevents_reservation_and_dispatch(self):
        gate = LaunchGate.prepare(**self.identity, backend="terminal")
        original = gate.barrier.path.read_bytes()
        with patch("todo_flow.launchers.subprocess.run") as dispatch:
            with self.assertRaisesRegex(TerminalCapacityError, "no capacity lease"):
                self.dispatch()
            dispatch.assert_not_called()
        self.assertEqual(gate.barrier.path.read_bytes(), original)
        self.assertFalse((self.root / "terminal-slots.json").exists())
        self.assertFalse((self.folder / "terminal-spec.json").exists())
        self.assertFalse((self.root / "delivery").exists())

    def test_dispatch_interruption_also_cancels_unconsumed_permit(self):
        with patch("todo_flow.launchers.subprocess.run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.dispatch()
        self.assert_late_delivery_rejected()
