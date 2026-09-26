import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import verification


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class VerificationCleanupTests(unittest.TestCase):
    def start(self, code):
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.dispose, proc)
        return proc

    def dispose(self, proc):
        # Test fixtures own these groups, including when the assertion fails.
        proc.poll()
        if verification.group_running(proc.pid):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate(timeout=5)

    def test_zombie_parent_is_reaped_without_sending_a_signal(self):
        proc = self.start("pass")
        deadline = time.monotonic() + 5
        while True:
            state = subprocess.run(
                ["ps", "-p", str(proc.pid), "-o", "stat="],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            if state.startswith("Z"):
                break
            if time.monotonic() >= deadline:
                self.fail("Fixture did not become a zombie")
            time.sleep(0.01)
        self.assertIsNone(proc.returncode)
        with patch("todo_flow.verification.os.killpg") as send:
            self.assertEqual(verification.stop_group(proc), ("", ""))
        send.assert_not_called()
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(verification.group_running(proc.pid))

    def test_parent_exit_does_not_hide_live_children_or_open_pipes(self):
        control = self.start("import time; time.sleep(60)")
        for open_pipes in (False, True):
            for ignore_term in (False, True):
                with self.subTest(open_pipes=open_pipes, ignore_term=ignore_term):
                    with tempfile.TemporaryDirectory() as tmp:
                        writes = Path(tmp) / "writes"
                        ready = Path(tmp) / "ready"
                        child = (
                            "import signal,time\n"
                            "from pathlib import Path\n"
                            + (
                                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                if ignore_term
                                else ""
                            )
                            + f"Path({str(ready)!r}).write_text('ready')\n"
                            + "while True:\n"
                            + f"    with open({str(writes)!r}, 'a') as output:\n"
                            + "        output.write('tick\\n')\n"
                            + "    time.sleep(.01)\n"
                        )
                        redirection = (
                            "" if open_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                        )
                        parent = (
                            "import subprocess,sys,time\n"
                            "from pathlib import Path\n"
                            f"subprocess.Popen([sys.executable,'-c',{child!r}]{redirection})\n"
                            f"while not Path({str(ready)!r}).exists(): time.sleep(.01)\n"
                        )
                        proc = self.start(parent)
                        self.assertEqual(proc.wait(timeout=5), 0)
                        self.assertTrue(verification.group_running(proc.pid))
                        verification.stop_group(proc)
                        self.assertFalse(verification.group_running(proc.pid))
                        before = writes.read_bytes() if writes.exists() else b""
                        time.sleep(0.1)
                        after = writes.read_bytes() if writes.exists() else b""
                        self.assertEqual(after, before, "Child wrote after cleanup returned")
                        self.assertIsNone(control.poll(), "Cleanup killed another group")

    def test_cooperative_parent_does_not_receive_kill(self):
        proc = self.start("import time; time.sleep(60)")
        with patch("todo_flow.verification.os.killpg", wraps=os.killpg) as send:
            verification.stop_group(proc)
        self.assertEqual([call.args[1] for call in send.call_args_list], [signal.SIGTERM])
        self.assertIsNotNone(proc.returncode)

    def test_signal_denial_is_a_cleanup_error(self):
        proc = self.start("import time; time.sleep(60)")
        with patch("todo_flow.verification.os.killpg", side_effect=PermissionError("denied")):
            with self.assertRaises(verification.VerificationCleanupError) as raised:
                verification.stop_group(proc)
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertIsNone(proc.poll())

    def test_inspection_failure_does_not_trigger_blind_signals(self):
        proc = self.start("import time; time.sleep(60)")
        with (
            patch("todo_flow.verification.subprocess.run", side_effect=OSError("ps unavailable")),
            patch("todo_flow.verification.os.killpg") as send,
        ):
            with self.assertRaises(verification.VerificationCleanupError):
                verification.stop_group(proc)
        send.assert_not_called()
        self.assertIsNone(proc.poll())

    def test_process_lookup_error_still_requires_exit_confirmation(self):
        proc = self.start("import time; time.sleep(60)")
        with (
            patch("todo_flow.verification.os.killpg", side_effect=ProcessLookupError),
            patch("todo_flow.verification.time.monotonic", side_effect=[0, 1, 2, 5]),
        ):
            with self.assertRaisesRegex(verification.VerificationCleanupError, "did not stop"):
                verification.stop_group(proc)
        self.assertIsNone(proc.poll())

    def test_timeout_is_preserved_after_confirmed_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(subprocess.TimeoutExpired):
                verification.run([sys.executable, "-c", "import time; time.sleep(1)"], tmp, 0.05)

    def test_interrupt_is_preserved_after_confirmed_cleanup(self):
        communicate = subprocess.Popen.communicate

        def interrupt_once(proc, *args, **kwargs):
            if kwargs.get("timeout") == 0.125:
                raise KeyboardInterrupt
            return communicate(proc, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(subprocess.Popen, "communicate", interrupt_once):
                with self.assertRaises(KeyboardInterrupt):
                    verification.run(
                        [sys.executable, "-c", "import time; time.sleep(1)"], tmp, 0.125
                    )

    def test_invalid_inspection_is_not_proof_of_exit(self):
        for output in ("", "unexpected output\n", "123\n", "123 S extra\n"):
            with self.subTest(output=output):
                result = subprocess.CompletedProcess(["ps"], 0, output, "")
                with patch("todo_flow.verification.subprocess.run", return_value=result):
                    with self.assertRaises(verification.VerificationCleanupError):
                        verification.group_running(123)

    def test_nonzero_inspection_is_not_proof_of_exit(self):
        result = subprocess.CompletedProcess(["ps"], 1, "", "denied")
        with patch("todo_flow.verification.subprocess.run", return_value=result):
            with self.assertRaises(verification.VerificationCleanupError):
                verification.group_running(123)
