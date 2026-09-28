"""Exercise file-backed logs through Engine handoffs and the real HTTP boundary."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import urlencode

import test_engine_verification_identity as engine_tests
from todo_flow import verification_identity
from todo_flow import verification_logs as logs
from todo_flow.engine import Engine
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Conflict, encode
from todo_flow.worker import worker_input


class VerificationLogConsumerTests(unittest.TestCase):
    verifier = engine_tests.EngineVerificationIdentityTests.verifier
    count = engine_tests.EngineVerificationIdentityTests.count

    def setUp(self):
        engine_tests.EngineVerificationIdentityTests.setUp(self)
        self.http = None

    def tearDown(self):
        if self.http is not None:
            self.http.terminate()
            self.http.communicate(timeout=5)
        engine_tests.EngineVerificationIdentityTests.tearDown(self)

    def get(self, query=""):
        if self.http is None:
            self.http = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "todo_flow",
                    "--state",
                    str(self.s.path),
                    "serve",
                    "--port",
                    "0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.url = self.http.stdout.readline().strip().split("Dashboard: ")[1]
        with urllib.request.urlopen(
            self.url + "/api/tracks/addition/evidence/verification" + query, timeout=10
        ) as response:
            body = response.read(200001)
        self.assertLess(len(body), 200000)
        return json.loads(body)["value"]

    def handoff(self, engine, task, workspace):
        folder = self.root / "handoff"
        folder.mkdir(exist_ok=True)
        value = worker_input(engine.context(task, workspace), folder)
        self.assertNotIn("verification_logs", value)
        return json.loads(Path(value["paths"]["verification_logs"]).read_text())

    def read(self, ref, stream="stdout", offset=0, limit=32, source="result"):
        return self.get(
            "?"
            + urlencode(
                dict(
                    stream=stream,
                    execution=ref["execution"],
                    source=source,
                    offset=offset,
                    limit=limit,
                )
            )
        )

    def assert_http_original(self, ref, stream, expected):
        digest = hashlib.sha256()
        offset = 0
        while offset < len(expected):
            part = self.read(ref, stream, offset, logs.MAX_READ_BYTES)
            chunk = base64.b64decode(part["base64"])
            self.assertEqual(part["reference"], ref)
            self.assertEqual(part["start"], offset)
            self.assertEqual(part["size"], len(expected))
            self.assertEqual(part["end"], offset + len(chunk))
            self.assertLessEqual(len(chunk), logs.MAX_READ_BYTES)
            self.assertTrue(chunk)
            digest.update(chunk)
            offset = part["end"]
        self.assertEqual(digest.digest(), hashlib.sha256(expected).digest())
        original = Path(ref["manifest"]).parent / (stream + ".log")
        self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), digest.digest())

    def test_engine_success_failure_timeout_reach_worker_and_http(self):
        engine, task, workspace = self.verifier()
        expected = {
            "stdout": ("OUT-BEGIN\n" + "한🙂" * 4000 + "\nOUT-END\n").encode(),
            "stderr": ("ERR-BEGIN\n" + "오류🙂" * 4000 + "\nERR-END\n").encode(),
        }
        source = (
            self.source
            + "import sys,time\n"
            + f"sys.stdout.buffer.write({expected['stdout']!r})\n"
            + f"sys.stderr.buffer.write({expected['stderr']!r})\n"
            + "sys.stdout.flush(); sys.stderr.flush()\n"
        )
        for mode, suffix in (
            ("success", ""),
            ("failure", "raise SystemExit(7)\n"),
            ("timeout", "time.sleep(60)\n"),
        ):
            with self.subTest(mode=mode):
                self.runner.write_text(source + suffix)
                engine.config["verify_timeout"] = 2
                record = engine.verify(task, workspace)
                self.assertEqual(record["ok"], mode == "success")
                if mode == "timeout":
                    self.assertEqual(record["error"], "TimeoutExpired")
                ref = record["logReference"]
                self.assertEqual(ref["attempt"], task["attempt"])
                persisted = json.loads(
                    (self.s.path / "attempts" / task["attempt"] / "verification.json").read_text()
                )
                self.assertEqual(persisted["logReference"], ref)
                worker = self.handoff(engine, task, workspace)
                value = self.get()
                self.assertEqual(value["logReference"], ref)
                self.assertEqual(value["logs"], worker)
                self.assertIsNone(worker["latestExecution"])
                summary = worker["result"]
                self.assertEqual(summary["complete"], mode == "success")
                self.assertEqual(summary["termination"]["state"], "confirmed")
                for stream, content in expected.items():
                    self.assertTrue(summary["streams"][stream]["truncated"])
                    self.assertLessEqual(
                        len(summary["streams"][stream]["text"].encode()),
                        logs.TAIL_BYTES + 6,
                    )
                    self.assert_http_original(ref, stream, content)
                engine.process_barrier(task["track"]).require_clear()

    def test_cached_result_keeps_its_reference_when_latest_execution_differs(self):
        engine, task, workspace = self.verifier("import sys\nprint(sys.argv[1])\n")
        command = engine.config["verify"]
        engine.config["verify"] = [*command, "FIRST"]
        first = engine.verify(task, workspace)
        self.assertTrue(first["ok"])
        # Keep the runner's metadata unchanged so restoring argv restores its identity.
        with patch.dict(engine.config, verify=[*command, "SECOND"]):
            second = engine.verify(task, workspace)
        self.assertTrue(second["ok"])
        self.assertNotEqual(first["logReference"], second["logReference"])
        engine.update(task, verification=encode(first))
        self.assertEqual(engine.verify(task, workspace), first)
        self.assertEqual(self.count(), 2)
        view = self.handoff(engine, task, workspace)
        self.assertEqual(view, self.get()["logs"])
        self.assertEqual(view["result"]["reference"], first["logReference"])
        self.assertEqual(view["latestExecution"]["reference"], second["logReference"])
        self.assertIn("FIRST", self.read(first["logReference"])["text"])
        self.assertIn("SECOND", self.read(second["logReference"], source="latest")["text"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.read(first["logReference"], source="latest")
        self.assertEqual(caught.exception.code, 400)

    def test_prelaunch_failure_does_not_adopt_previous_logs(self):
        engine, task, workspace = self.verifier("print('PREVIOUS')\n")
        first = engine.verify(task, workspace)
        self.runner.write_text(self.source + "# invalidate the cache\n")
        capture = verification_identity.capture
        observations = 0

        def changed(*args, **kwargs):
            nonlocal observations
            observations += 1
            if observations == 2:
                self.runner.write_text(self.source + "# changed before launch\n")
            return capture(*args, **kwargs)

        with patch.object(verification_identity, "capture", side_effect=changed):
            record = engine.verify(task, workspace)
        self.assertFalse(record["ok"])
        self.assertIsNone(record["logReference"])
        self.assertEqual(self.count(), 1)
        view = self.handoff(engine, task, workspace)
        self.assertEqual(view, self.get()["logs"])
        self.assertEqual(view["result"]["format"], "not-started")
        self.assertEqual(view["latestExecution"]["reference"], first["logReference"])

    def test_legacy_is_not_upgraded_from_a_newer_log_pointer(self):
        engine, task, workspace = self.verifier("print('NEW')\n")
        legacy = {"ok": False, "output": "legacy tail"}
        engine.update(task, verification=encode(legacy))
        self.assertEqual(self.get()["logs"]["result"]["format"], "legacy-tail-only")
        self.assertEqual(
            self.handoff(engine, task, workspace)["result"]["format"], "legacy-tail-only"
        )
        current = engine.verify(task, workspace)
        engine.update(task, verification=encode(legacy))
        view = self.get()["logs"]
        self.assertEqual(view["result"]["format"], "legacy-tail-only")
        self.assertFalse(view["result"]["complete"])
        self.assertEqual(view["latestExecution"]["reference"], current["logReference"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.read(current["logReference"])
        self.assertEqual(caught.exception.code, 400)

    def test_cancelled_engine_output_remains_visible_without_a_result(self):
        engine, task, workspace = self.verifier(
            "import time\nprint('BEFORE-CANCEL',flush=True)\ntime.sleep(60)\n"
        )
        original = engine.check_claim

        def cancel_after_output(current):
            latest = logs.latest(self.s.path, "addition")
            text = (latest or {}).get("streams", {}).get("stdout", {}).get("text", "")
            if "BEFORE-CANCEL" in text:
                self.s.control("addition", "cancel")
            original(current)

        with patch.object(engine, "check_claim", side_effect=cancel_after_output):
            with self.assertRaises(Conflict):
                engine.verify(task, workspace)
        self.assertIsNone(self.s.track("addition")["verification"])
        view = self.handoff(Engine(self.s), task, workspace)
        self.assertEqual(view, self.get()["logs"])
        self.assertIsNone(view["result"])
        latest = view["latestExecution"]
        self.assertFalse(latest["complete"])
        self.assertIn("BEFORE-CANCEL", self.read(latest["reference"], source="latest")["text"])
        engine.process_barrier(task["track"]).require_clear()

    def test_driver_kill_is_visible_to_fresh_engine_and_http_readers(self):
        engine, task, workspace = self.verifier(
            "import time\nprint('BEFORE-KILL',flush=True)\ntime.sleep(60)\n"
        )
        engine.config["verify_timeout"] = 8
        program = (
            "import json,sys; from todo_flow.engine import Engine; "
            "from todo_flow.store import Store; "
            "engine=Engine(Store(sys.argv[1])); "
            "engine.config.update(json.loads(sys.argv[2])); "
            "engine.verify(json.loads(sys.argv[3]),sys.argv[4])"
        )
        driver = subprocess.Popen(
            [
                sys.executable,
                "-c",
                program,
                str(self.s.path),
                json.dumps(engine.config),
                json.dumps(task),
                str(workspace),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                latest = logs.latest(self.s.path, "addition")
                text = (latest or {}).get("streams", {}).get("stdout", {}).get("text", "")
                if "BEFORE-KILL" in text:
                    break
                if driver.poll() is not None:
                    self.fail("Driver exited before its verifier produced output")
                time.sleep(0.02)
            else:
                self.fail("Verifier did not produce output")
            driver.kill()
            driver.wait(timeout=5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                latest = logs.latest(self.s.path, "addition")
                if latest.get("termination", {}).get("state") == "confirmed":
                    break
                time.sleep(0.02)
            else:
                self.fail("Supervisor did not confirm cleanup")
            self.assertIsNone(self.s.track("addition")["verification"])
            view = self.handoff(Engine(self.s), task, workspace)
            self.assertEqual(view, self.get()["logs"])
            latest = view["latestExecution"]
            self.assertEqual(latest["phase"], "interrupted")
            self.assertFalse(latest["complete"])
            self.assertEqual(latest["termination"]["completion"], "driver-disconnected")
            self.assertIn("BEFORE-KILL", self.read(latest["reference"], source="latest")["text"])
            LaunchGate(
                self.s.path, "addition", task["attempt"], latest["reference"]["execution"]
            ).barrier.require_clear()
        finally:
            if driver.poll() is None:
                driver.kill()
                driver.wait(timeout=5)

    def test_http_rejects_unbounded_ranges_and_reports_missing_or_future_logs(self):
        engine, task, workspace = self.verifier("print('DATA')\n")
        record = engine.verify(task, workspace)
        ref = record["logReference"]
        for query in (
            "?stream=stdout",
            "?stream=stdout&execution=wrong",
            "?path=/etc/passwd",
            "?" + urlencode(dict(stream="stdout", execution=ref["execution"], offset=-1)),
            "?"
            + urlencode(
                dict(stream="stdout", execution=ref["execution"], limit=logs.MAX_READ_BYTES + 1)
            ),
        ):
            with self.subTest(query=query):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get(query)
                self.assertEqual(caught.exception.code, 400)
        path = Path(ref["manifest"]).parent / "stdout.log"
        path.unlink()
        summary = self.get()["logs"]["result"]
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["phase"], "partial")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.read(ref)
        self.assertEqual(caught.exception.code, 400)
        manifest = Path(ref["manifest"])
        metadata = json.loads(manifest.read_text())
        metadata["version"] = 999
        manifest.write_text(json.dumps(metadata))
        before = manifest.read_bytes()
        self.assertEqual(self.get()["logs"]["result"]["format"], "unsupported")
        self.assertEqual(self.handoff(engine, task, workspace)["result"]["format"], "unsupported")
        self.assertEqual(manifest.read_bytes(), before)
