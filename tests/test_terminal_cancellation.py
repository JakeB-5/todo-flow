import fcntl
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

from todo_flow import terminal_worker, verification
from todo_flow.launchers import TerminalProcess


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class TerminalCancellationTests(unittest.TestCase):
    def test_stale_receipt_never_signals_an_unrelated_process(self):
        control = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                receipt = {
                    "status": "running",
                    "pid": control.pid,
                    "runner_pid": control.pid,
                }
                path = folder / "terminal-process.json"
                path.write_text(json.dumps(receipt))
                with patch("todo_flow.verification.os.killpg") as send:
                    with self.assertRaisesRegex(
                        verification.VerificationCleanupError, "bridge stopped"
                    ):
                        TerminalProcess(folder).stop()
                    send.assert_not_called()
                self.assertIsNone(control.poll())
                self.assertEqual(json.loads(path.read_text()), receipt)
                self.assertTrue((folder / "terminal-cancelled").exists())
        finally:
            control.kill()
            control.wait(timeout=5)

    def test_launch_in_progress_without_receipt_is_not_confirmed_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with (folder / "terminal-run.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(
                    verification.VerificationCleanupError, "did not confirm"
                ):
                    TerminalProcess(folder).stop(timeout=0)
            # The bridge has released its lock without spawning; late delivery
            # is now fenced by the durable cancellation marker.
            TerminalProcess(folder).stop(timeout=0)
            self.assertTrue((folder / "terminal-cancelled").exists())

    def test_failed_and_legacy_exit_receipts_are_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "terminal-process.json"
            for receipt, message in (
                (
                    {"status": "cleanup_failed", "cleanup_error": "inspection unavailable"},
                    "inspection unavailable",
                ),
                ({"status": "exited", "returncode": 0}, "does not confirm"),
            ):
                with self.subTest(receipt=receipt):
                    path.write_text(json.dumps(receipt))
                    process = TerminalProcess(folder)
                    with self.assertRaisesRegex(verification.VerificationCleanupError, message):
                        process.poll()
                    with self.assertRaisesRegex(verification.VerificationCleanupError, message):
                        process.stop(timeout=0)
                    self.assertEqual(json.loads(path.read_text()), receipt)
                    self.assertIsNone(process.returncode)

    def test_cancellation_waits_for_term_ignoring_descendants_and_output(self):
        for open_pipes in (False, True):
            with self.subTest(open_pipes=open_pipes), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                child = (
                    "import signal,time\n"
                    "from pathlib import Path\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "Path('ready').touch()\n"
                    "while True:\n"
                    "    with open('writes', 'a') as output: output.write('tick\\n')\n"
                    "    time.sleep(.01)\n"
                )
                redirect = (
                    "" if open_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                )
                parent = (
                    "import subprocess,sys,time\n"
                    f"subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})\n"
                    "time.sleep(60)\n"
                )
                (folder / "input.json").write_text("{}")
                spec = folder / "terminal-spec.json"
                spec.write_text(
                    json.dumps(
                        {
                            "argv": [sys.executable, "-c", parent],
                            "cwd": tmp,
                            "title": "Cancellation fixture",
                        }
                    )
                )
                bridge = subprocess.Popen(
                    [sys.executable, terminal_worker.__file__, str(spec)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                process = TerminalProcess(folder)
                try:
                    deadline = time.monotonic() + 10
                    while not (folder / "ready").exists():
                        if bridge.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Bridge did not start the child fixture")
                        time.sleep(0.01)
                    # The driver never signals receipt PIDs. The separate bridge
                    # receives cancellation and uses its own live Popen handle.
                    with patch("todo_flow.verification.os.killpg") as send:
                        process.stop()
                        send.assert_not_called()
                    receipt = process.receipt()
                    self.assertEqual(process.returncode, 130)
                    self.assertTrue(receipt["cleanup_confirmed"])
                    self.assertFalse(verification.group_running(receipt["pid"]))
                    writes = folder / "writes"
                    before = writes.read_bytes() if writes.exists() else b""
                    time.sleep(0.1)
                    self.assertEqual(writes.read_bytes() if writes.exists() else b"", before)
                    self.assertEqual(bridge.wait(timeout=5), 130)
                finally:
                    # These PIDs belong to this test's freshly launched fixture.
                    pid = process.receipt().get("pid")
                    if pid:
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if bridge.poll() is None:
                        bridge.kill()
                    bridge.wait(timeout=5)
