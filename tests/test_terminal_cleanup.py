import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from todo_flow import terminal_worker, verification


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class TerminalCleanupTests(unittest.TestCase):
    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for fixture")
            time.sleep(0.01)

    def receipt(self, folder):
        path = folder / "terminal-process.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def dispose(self, bridge, folder):
        # Fixtures own both processes; clean them even after an assertion fails.
        receipt = self.receipt(folder)
        pid = receipt.get("pid")
        if pid and verification.group_running(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if bridge.poll() is None:
            bridge.kill()
        bridge.wait(timeout=5)

    def start(self, folder, parent, *, fail_inspection=False):
        (folder / "input.json").write_text("{}")
        spec = folder / "terminal-spec.json"
        spec.write_text(
            json.dumps(
                {
                    "argv": [sys.executable, "-c", parent],
                    "cwd": str(folder),
                    "title": "Cleanup fixture",
                }
            )
        )
        if fail_inspection:
            bootstrap = (
                "import sys\n"
                f"sys.path.insert(0, {str(Path(terminal_worker.__file__).parent)!r})\n"
                "import terminal_worker, verification\n"
                "def unavailable(pgid):\n"
                "    raise verification.VerificationCleanupError('inspection unavailable')\n"
                "verification.group_running = unavailable\n"
                "raise SystemExit(terminal_worker.main(sys.argv[1]))\n"
            )
            argv = [sys.executable, "-c", bootstrap, str(spec)]
        else:
            argv = [sys.executable, terminal_worker.__file__, str(spec)]
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def test_parent_exit_and_interruption_clean_descendants(self):
        control = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            start_new_session=True,
        )
        try:
            for outcome in ("success", "error", "interrupt"):
                for open_pipes in (False, True):
                    for ignore_term in (False, True):
                        with self.subTest(
                            outcome=outcome, open_pipes=open_pipes, ignore_term=ignore_term
                        ):
                            with tempfile.TemporaryDirectory() as tmp:
                                folder = Path(tmp)
                                child = (
                                    "import signal,time\n"
                                    "from pathlib import Path\n"
                                    + (
                                        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                        if ignore_term
                                        else ""
                                    )
                                    + "Path('ready').touch()\n"
                                    + "while True:\n"
                                    + "    with open('writes', 'a') as output:\n"
                                    + "        output.write('tick\\n')\n"
                                    + "    time.sleep(.01)\n"
                                )
                                redirect = (
                                    ""
                                    if open_pipes
                                    else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                                )
                                ending = {
                                    "success": "raise SystemExit(0)\n",
                                    "error": "raise SystemExit(7)\n",
                                    "interrupt": "time.sleep(60)\n",
                                }[outcome]
                                parent = (
                                    "import subprocess,sys,time\n"
                                    "from pathlib import Path\n"
                                    f"subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})\n"
                                    "while not Path('ready').exists(): time.sleep(.01)\n"
                                    + ending
                                )
                                bridge = self.start(folder, parent)
                                try:
                                    if outcome == "interrupt":
                                        self.wait_until(
                                            lambda: (folder / "ready").exists()
                                            and self.receipt(folder).get("status") == "running"
                                        )
                                        bridge.send_signal(signal.SIGTERM)
                                    expected = {"success": 0, "error": 7, "interrupt": 130}
                                    self.assertEqual(bridge.wait(timeout=15), expected[outcome])
                                    receipt = self.receipt(folder)
                                    self.assertEqual(receipt["status"], "exited")
                                    self.assertEqual(receipt["returncode"], expected[outcome])
                                    self.assertFalse(verification.group_running(receipt["pid"]))
                                    writes = folder / "writes"
                                    before = writes.read_bytes() if writes.exists() else b""
                                    time.sleep(0.1)
                                    after = writes.read_bytes() if writes.exists() else b""
                                    self.assertEqual(before, after)
                                    self.assertIsNone(control.poll())
                                finally:
                                    self.dispose(bridge, folder)
        finally:
            if control.poll() is None:
                control.kill()
            control.wait(timeout=5)

    def test_inspection_failure_preserves_unconfirmed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            parent = (
                "import subprocess,sys\n"
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
            )
            bridge = self.start(folder, parent, fail_inspection=True)
            try:
                self.assertNotEqual(bridge.wait(timeout=15), 0)
                receipt = self.receipt(folder)
                self.assertEqual(receipt["status"], "cleanup_failed")
                self.assertIn("inspection unavailable", receipt["cleanup_error"])
                self.assertNotIn("returncode", receipt)
                self.assertTrue(verification.group_running(receipt["pid"]))
            finally:
                self.dispose(bridge, folder)
