"""Original bytes remain independently readable through bounded log references."""

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow import verification
from todo_flow import verification_logs as logs
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate


class VerificationLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.identity = {
            "directory": str(self.directory),
            "track": "track",
            "attempt": "attempt",
            "execution": "verification",
        }

    def run_command(self, source, timeout=5):
        return verification.run(
            [sys.executable, "-c", source],
            self.directory,
            timeout,
            launch_identity=self.identity,
        )

    def summary(self):
        return logs.describe(self.directory, logs.reference(self.identity))

    def assert_original(self, name, expected):
        ref = logs.reference(self.identity)
        path = Path(ref["manifest"]).parent / (name + ".log")
        digest = hashlib.sha256(expected).hexdigest()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        actual = hashlib.sha256()
        offset = 0
        while offset < len(expected):
            part = logs.read_range(self.directory, ref, name, offset, logs.MAX_READ_BYTES)
            data = base64.b64decode(part["base64"])
            self.assertEqual(part["start"], offset)
            self.assertEqual(part["end"], offset + len(data))
            self.assertLessEqual(len(data), logs.MAX_READ_BYTES)
            self.assertLess(len(json.dumps(part).encode()), 150000)
            self.assertTrue(data)
            actual.update(data)
            offset = part["end"]
        self.assertEqual(actual.hexdigest(), digest)
        first = logs.read_range(self.directory, ref, name, 0, 32)
        last = logs.read_range(self.directory, ref, name, len(expected) - 32, 32)
        self.assertEqual(base64.b64decode(first["base64"]), expected[:32])
        self.assertEqual(base64.b64decode(last["base64"]), expected[-32:])

    def test_success_failure_and_timeout_preserve_both_multibyte_originals(self):
        stdout = ("OUT-BEGIN\n" + "한🙂" * 40000 + "\nOUT-END\n").encode()
        stderr = ("ERR-BEGIN\n" + "오류🙂" * 40000 + "\nERR-END\n").encode()
        source = (
            "import sys,time\n"
            "sys.stdout.buffer.write(('OUT-BEGIN\\n'+'한🙂'*40000+'\\nOUT-END\\n').encode())\n"
            "sys.stderr.buffer.write(('ERR-BEGIN\\n'+'오류🙂'*40000+'\\nERR-END\\n').encode())\n"
            "sys.stdout.flush(); sys.stderr.flush()\n"
        )
        for mode, suffix in (
            ("success", ""),
            ("failure", "raise SystemExit(7)\n"),
            ("timeout", "time.sleep(60)\n"),
        ):
            with self.subTest(mode=mode):
                self.identity["execution"] = mode
                if mode == "success":
                    output = self.run_command(source + suffix)
                    self.assertLessEqual(len(output.encode()), 2 * logs.TAIL_BYTES + 12)
                    self.assertIn("OUT-END", output)
                    self.assertIn("ERR-END", output)
                elif mode == "failure":
                    with self.assertRaisesRegex(RuntimeError, "ERR-END"):
                        self.run_command(source + suffix)
                else:
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        self.run_command(source + suffix, timeout=2)
                    self.assertIn("OUT-END", caught.exception.output)
                    self.assertIn("ERR-END", caught.exception.stderr)
                summary = self.summary()
                self.assertEqual(summary["reference"]["attempt"], "attempt")
                self.assertEqual(summary["reference"]["execution"], mode)
                self.assertEqual(summary["complete"], mode == "success")
                self.assertEqual(summary["termination"]["state"], "confirmed")
                self.assertTrue(summary["streams"]["stdout"]["truncated"])
                self.assertLess(len(json.dumps(summary).encode()), 80000)
                self.assert_original("stdout", stdout)
                self.assert_original("stderr", stderr)
                pointer = self.directory / "attempts/attempt/verification-logs.json"
                self.assertEqual(json.loads(pointer.read_text()), summary["reference"])
                LaunchGate(**self.identity).barrier.require_clear()

    def test_summary_and_ranges_never_issue_unbounded_reads(self):
        self.run_command("import sys; sys.stdout.write('한🙂'*100000)")
        original = logs._open
        requests = []

        class Reader:
            def __init__(self, source, limit):
                self.source = source
                self.limit = limit

            def read(self, amount=-1):
                if not 0 <= amount <= self.limit:
                    raise AssertionError(f"Unbounded read: {amount}")
                requests.append(amount)
                return self.source.read(amount)

            def seek(self, offset):
                return self.source.seek(offset)

            def fileno(self):
                return self.source.fileno()

        @contextmanager
        def bounded(directory, path):
            with original(directory, path) as source:
                limit = (
                    logs.MAX_READ_BYTES if str(path).endswith(".log") else logs.METADATA_BYTES + 1
                )
                yield Reader(source, limit)

        with patch.object(logs, "_open", bounded):
            self.assertTrue(self.summary()["complete"])
            part = logs.read_range(
                self.directory, logs.reference(self.identity), "stdout", 1, logs.MAX_READ_BYTES
            )
        self.assertTrue(requests)
        self.assertEqual(len(base64.b64decode(part["base64"])), logs.MAX_READ_BYTES)
        for offset, limit in ((-1, 1), (0, 0), (0, logs.MAX_READ_BYTES + 1)):
            with self.assertRaises(ValueError):
                logs.read_range(
                    self.directory, logs.reference(self.identity), "stdout", offset, limit
                )

    def test_storage_failure_keeps_bytes_and_does_not_claim_complete_output(self):
        with patch.object(logs.LogCapture, "_seal", side_effect=OSError(28, "disk full")):
            with self.assertRaisesRegex(logs.VerificationLogError, "disk full"):
                self.run_command("print('saved-before-storage-error', flush=True)")
        summary = self.summary()
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["phase"], "error")
        self.assertIn("disk full", summary["diagnostic"])
        self.assertIn("saved-before-storage-error", summary["streams"]["stdout"]["text"])
        self.assertEqual(summary["termination"]["state"], "confirmed")
        LaunchGate(**self.identity).barrier.require_clear()

    def test_producer_short_write_is_preserved_as_partial_output(self):
        source = (
            "import os,resource\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE,(4096,4096))\n"
            "os.write(1,b'x'*8192)\n"
            "os.write(1,b'y')\n"
        )
        with self.assertRaises(RuntimeError):
            self.run_command(source)
        summary = self.summary()
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["phase"], "partial")
        self.assertEqual(summary["streams"]["stdout"]["size"], 4096)
        self.assert_original("stdout", b"x" * 4096)
        LaunchGate(**self.identity).barrier.require_clear()

    def test_escape_and_replaced_files_are_not_complete_evidence(self):
        self.run_command("print('original')")
        ref = logs.reference(self.identity)
        outside = self.directory / "private.txt"
        outside.write_text("DO-NOT-EXPOSE")
        escaped = {**ref, "manifest": str(outside)}
        self.assertEqual(logs.describe(self.directory, escaped)["format"], "unavailable")
        with self.assertRaises(ValueError):
            logs.read_range(self.directory, escaped, "stdout")
        output = Path(ref["manifest"]).parent / "stdout.log"
        output.write_text("short")
        self.assertFalse(self.summary()["complete"])
        output.unlink()
        output.symlink_to(outside)
        summary = self.summary()
        self.assertFalse(summary["complete"])
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(summary))
        with self.assertRaises(ValueError):
            logs.read_range(self.directory, ref, "stdout")

    def test_symlink_output_directory_prevents_launch(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (self.directory / "process-output").symlink_to(outside, target_is_directory=True)
        with patch("todo_flow.supervised_process.SupervisedProcess") as spawn:
            with self.assertRaises(logs.VerificationLogError):
                self.run_command("print('must not run')")
            spawn.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_legacy_missing_and_future_formats_are_distinct(self):
        self.assertEqual(logs.describe(self.directory, None)["format"], "legacy-tail-only")
        ref = logs.reference(self.identity)
        self.assertEqual(logs.describe(self.directory, ref)["format"], "unavailable")
        self.run_command("print('current')")
        manifest = Path(ref["manifest"])
        record = json.loads(manifest.read_text())
        record["version"] = 999
        manifest.write_text(json.dumps(record))
        before = manifest.read_bytes()
        summary = self.summary()
        self.assertEqual(summary["format"], "unsupported")
        self.assertFalse(summary["complete"])
        with self.assertRaises(logs.UnsupportedLogFormat):
            logs.read_range(self.directory, ref, "stdout")
        self.assertEqual(manifest.read_bytes(), before)

    def test_driver_kill_leaves_a_reference_readable_in_a_fresh_process(self):
        source_root = str(Path(verification.__file__).resolve().parents[1])
        bootstrap = f"import sys; sys.path.insert(0,{source_root!r}); "
        child = "import time; print('BEFORE-DRIVER-KILL',flush=True); time.sleep(60)"
        program = (
            bootstrap
            + "from todo_flow.verification import run; "
            + f"run([sys.executable,'-c',{child!r}],{str(self.directory)!r},8,"
            + f"launch_identity={self.identity!r})"
        )
        driver = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                summary = logs.latest(self.directory, "track")
                tail = (summary or {}).get("streams", {}).get("stdout", {}).get("text", "")
                if "BEFORE-DRIVER-KILL" in tail:
                    break
                if driver.poll() is not None:
                    self.fail("Driver exited before producing its marker")
                time.sleep(0.02)
            else:
                self.fail("Verifier did not produce its marker")
            self.assertFalse(summary["complete"])
            driver.kill()
            driver.wait(timeout=5)
            gate = LaunchGate(**self.identity)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if gate._event()["state"] == "confirmed":
                        break
                except ProcessBarrierError:
                    pass
                time.sleep(0.02)
            else:
                self.fail("Supervisor did not confirm cleanup after driver loss")
            reader = (
                bootstrap
                + "import json; from todo_flow.verification_logs import latest; "
                + f"print(json.dumps(latest({str(self.directory)!r},'track')))"
            )
            recovered = json.loads(subprocess.check_output([sys.executable, "-c", reader]))
            self.assertEqual(recovered["phase"], "interrupted")
            self.assertFalse(recovered["complete"])
            self.assertEqual(recovered["termination"]["completion"], "driver-disconnected")
            self.assertIn("BEFORE-DRIVER-KILL", recovered["streams"]["stdout"]["text"])
            gate.barrier.require_clear()
        finally:
            if driver.poll() is None:
                driver.kill()
                driver.wait(timeout=5)
