"""The real terminal bridge consumes a pre-dispatch permit and fails closed."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_flow
from todo_flow import terminal_worker, verification
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Store


BOOTSTRAP = """
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import terminal_worker

spec, failed_state = sys.argv[2:]
folder = Path(spec).parent
original_spawn = terminal_worker.subprocess.Popen
original_advance = terminal_worker.LaunchGate._advance

def spawn(*args, **kwargs):
    proc = original_spawn(*args, **kwargs)
    if kwargs.get("start_new_session"):
        (folder / "fixture-pgid").write_text(str(proc.pid))
    return proc

def advance(self, event, state, reason, evidence):
    if state == failed_state:
        deadline = time.monotonic() + 10
        while not (folder / "ready").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Fixture descendant did not start")
            time.sleep(.01)
        raise OSError("injected journal failure: " + state)
    return original_advance(self, event, state, reason, evidence)

terminal_worker.subprocess.Popen = spawn
terminal_worker.LaunchGate._advance = advance
raise SystemExit(terminal_worker.main(spec))
"""


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class TerminalLaunchGateTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self, folder, code, execution="terminal"):
        task = self.s.claim("driver")
        gate = LaunchGate.prepare(
            self.s.path, "addition", task["attempt"], execution, backend="terminal"
        )
        (folder / "input.json").write_text("{}")
        spec = folder / "terminal-spec.json"
        spec.write_text(
            json.dumps(
                {
                    "argv": [sys.executable, "-c", code],
                    "cwd": str(folder),
                    "title": "Launch gate fixture",
                    "launch_identity": {
                        "directory": str(self.s.path),
                        "track": "addition",
                        "attempt": task["attempt"],
                        "execution": execution,
                    },
                }
            )
        )
        return task, gate, spec

    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for terminal fixture")
            time.sleep(0.01)

    def assert_recovery_blocked(self, task):
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        engine = Engine(Store(self.s.path))
        before = self.s.snapshot()
        engine.reconcile()
        self.assertEqual(before["tasks"], self.s.snapshot()["tasks"])
        self.assertEqual(before["attempts"], self.s.snapshot()["attempts"])
        with patch.object(engine, "ensure_workspace") as workspace:
            engine.execute(task)
        workspace.assert_not_called()
        with self.assertRaises(ProcessBarrierError):
            engine.process_barrier("addition").require_clear()

    def test_cancelled_permit_rejects_real_file_entrypoint(self):
        self.s.start("addition")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _, gate, spec = self.prepare(
                folder, "from pathlib import Path; Path('unexpected-spawn').touch()"
            )
            gate.cancel_pending()
            result = subprocess.run(
                [sys.executable, terminal_worker.__file__, str(spec)],
                cwd=folder,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("ImportError", result.stderr)
            self.assertFalse((folder / "unexpected-spawn").exists())
            self.assertEqual(gate.barrier.history()[-1]["evidence"]["outcome"], "not-spawned")
            gate.barrier.require_clear()
            receipt = json.loads((folder / "terminal-process.json").read_text())
            self.assertNotEqual(receipt["status"], "exited")

    def exercise_live_group(self, failed_state):
        self.s.start("addition")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            child = (
                "import signal,time\n"
                "from pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "Path('writes').write_text('ready\\n')\n"
                "Path('ready').touch()\n"
                "while True:\n"
                "    with open('writes', 'a') as output: output.write('tick\\n')\n"
                "    time.sleep(.01)\n"
            )
            parent = (
                "import subprocess,sys,time\n"
                "from pathlib import Path\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                "while not Path('ready').exists(): time.sleep(.01)\n"
                + ("time.sleep(120)\n" if failed_state == "kill" else "")
            )
            task, gate, spec = self.prepare(folder, parent)
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    BOOTSTRAP,
                    str(Path(terminal_worker.__file__).parent),
                    str(spec),
                    failed_state,
                ],
                cwd=folder,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                if failed_state == "kill":
                    self.wait_until(
                        lambda: (
                            (folder / "ready").exists()
                            and gate.barrier.history()[-1]["state"] == "running"
                        )
                    )
                    bridge.kill()
                _, stderr = bridge.communicate(timeout=20)
                self.assertNotEqual(bridge.returncode, 0, stderr)
                self.assertTrue((folder / "ready").exists(), stderr)
                pgid = int((folder / "fixture-pgid").read_text())
                if failed_state == "kill":
                    self.assertTrue(verification.group_running(pgid))
                    self.assertEqual(gate.barrier.history()[-1]["state"], "running")
                else:
                    self.assertFalse(verification.group_running(pgid), stderr)
                    self.assertEqual(gate.barrier.history()[-1]["state"], "unknown")
                    before = (folder / "writes").read_bytes()
                    time.sleep(0.1)
                    self.assertEqual(before, (folder / "writes").read_bytes())
                    receipt = json.loads((folder / "terminal-process.json").read_text())
                    self.assertEqual(receipt["status"], "cleanup_failed")
                    self.assertNotIn("cleanup_confirmed", receipt)
                self.assert_recovery_blocked(task)
                with self.assertRaises(ProcessBarrierError):
                    gate.cancel_pending()
            finally:
                # Dispose only this test's freshly created group, never a stored
                # application receipt. The production recovery sends no signal.
                identity = folder / "fixture-pgid"
                if identity.exists():
                    pgid = int(identity.read_text())
                    if verification.group_running(pgid):
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                if bridge.poll() is None:
                    bridge.kill()
                bridge.communicate(timeout=5)

    def test_running_journal_failure_still_cleans_spawned_descendant(self):
        self.exercise_live_group("running")

    def test_cleaning_journal_failure_still_cleans_spawned_descendant(self):
        self.exercise_live_group("cleaning")

    def test_bridge_death_with_live_descendant_blocks_new_engine(self):
        self.exercise_live_group("kill")

    def test_cleanup_without_ownership_proof_never_confirms_exit(self):
        self.exercise_live_group("none")
