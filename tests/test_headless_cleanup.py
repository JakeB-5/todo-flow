import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import verification, worker


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class HeadlessCleanupTests(unittest.TestCase):
    def dispose(self, proc):
        # These are fixture-owned Popen objects, never PIDs from old receipts.
        proc.poll()
        if verification.group_running(proc.pid):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)

    def run_fixture(self, root, code, heartbeat, *, timeout=10):
        config = {
            "worker_protocol": 2,
            "worker_launcher": "headless",
            "worker_timeout": timeout,
            "worker": {"type": "command", "argv": [sys.executable, "-c", code]},
        }
        return worker.run_worker(
            config,
            {"workspace": str(root)},
            {"attempt": "headless", "kind": "work"},
            root / "state",
            heartbeat,
        )

    def test_all_exit_paths_stop_descendants_before_return(self):
        control = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            start_new_session=True,
        )
        try:
            for outcome in ("success", "error", "timeout", "interrupt"):
                for inherit_output in (False, True):
                    for ignore_term in (False, True):
                        with self.subTest(
                            outcome=outcome,
                            inherit_output=inherit_output,
                            ignore_term=ignore_term,
                        ):
                            with tempfile.TemporaryDirectory() as tmp:
                                root = Path(tmp)
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
                                    if inherit_output
                                    else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                                )
                                ending = {
                                    "success": "raise SystemExit(0)\n",
                                    "error": "raise SystemExit(7)\n",
                                    "timeout": "time.sleep(60)\n",
                                    "interrupt": "time.sleep(60)\n",
                                }[outcome]
                                parent = (
                                    "import json,subprocess,sys,time\n"
                                    "from pathlib import Path\n"
                                    f"subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})\n"
                                    "while not Path('ready').exists(): time.sleep(.01)\n"
                                    "print(json.dumps({'summary':'fixture'}), flush=True)\n"
                                    + ending
                                )
                                processes = []
                                original_popen = subprocess.Popen

                                def capture(*args, **kwargs):
                                    proc = original_popen(*args, **kwargs)
                                    if kwargs.get("start_new_session"):
                                        processes.append(proc)
                                    return proc

                                def heartbeat(pid):
                                    deadline = time.monotonic() + 5
                                    while not (root / "ready").exists():
                                        if time.monotonic() >= deadline:
                                            self.fail("Child did not become ready")
                                        time.sleep(.01)
                                    if outcome == "interrupt":
                                        raise KeyboardInterrupt

                                try:
                                    with patch("todo_flow.worker.subprocess.Popen", capture):
                                        if outcome == "success":
                                            result = self.run_fixture(root, parent, heartbeat)
                                            self.assertEqual(result["summary"], "fixture")
                                        else:
                                            expected = {
                                                "error": RuntimeError,
                                                "timeout": TimeoutError,
                                                "interrupt": KeyboardInterrupt,
                                            }[outcome]
                                            with self.assertRaises(expected):
                                                self.run_fixture(
                                                    root,
                                                    parent,
                                                    heartbeat,
                                                    timeout=0 if outcome == "timeout" else 10,
                                                )
                                    self.assertEqual(len(processes), 1)
                                    proc = processes[0]
                                    self.assertIsNotNone(proc.poll())
                                    self.assertFalse(verification.group_running(proc.pid))
                                    writes = root / "writes"
                                    before = writes.read_bytes() if writes.exists() else b""
                                    time.sleep(.1)
                                    after = writes.read_bytes() if writes.exists() else b""
                                    self.assertEqual(before, after)
                                    self.assertIsNone(control.poll())
                                finally:
                                    for proc in processes:
                                        self.dispose(proc)
        finally:
            self.dispose(control)

    def test_unconfirmed_cleanup_does_not_return_a_successful_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "todo_flow.verification.group_running",
                side_effect=verification.VerificationCleanupError("inspection unavailable"),
            ):
                with self.assertRaisesRegex(
                    verification.VerificationCleanupError, "inspection unavailable"
                ):
                    self.run_fixture(
                        root,
                        "import json; print(json.dumps({'summary':'must not return'}))",
                        lambda _: None,
                    )

    def test_signal_denial_is_not_reported_as_an_ordinary_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = []
            original_popen = subprocess.Popen

            def capture(*args, **kwargs):
                proc = original_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    processes.append(proc)
                return proc

            try:
                with (
                    patch("todo_flow.worker.subprocess.Popen", capture),
                    patch("todo_flow.verification.os.killpg", side_effect=PermissionError),
                ):
                    with self.assertRaises(verification.VerificationCleanupError) as raised:
                        self.run_fixture(
                            root,
                            "import time; time.sleep(60)",
                            lambda _: None,
                            timeout=0,
                        )
                    self.assertIsInstance(raised.exception.__cause__, PermissionError)
                self.assertEqual(len(processes), 1)
                self.assertIsNone(processes[0].poll())
            finally:
                for proc in processes:
                    self.dispose(proc)
