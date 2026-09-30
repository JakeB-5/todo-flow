"""Cancellation through real owned processes and durable restart boundaries."""

import concurrent.futures
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

import test_flow
import test_native_execution
from todo_flow.adapters import file_lock
from todo_flow.cancel_execution import recover_request
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory, launch_identity
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Conflict, Store
from todo_flow.verification import run


WRITER = """import json,sys,time
from pathlib import Path
writes,release=map(Path,sys.argv[1:])
while not release.exists():
    with writes.open('a') as stream: stream.write('tick\\n')
    time.sleep(.01)
print(json.dumps({'summary':'preserved','next':[{'kind':'work','purpose':'followup'}]}))
"""

DRIVER = """import json,subprocess,sys,time
from todo_flow.engine import Engine
from todo_flow.process_inventory import launch_identity
from todo_flow.store import Store
from todo_flow.supervised_process import SupervisedProcess
store=Store(sys.argv[1]); task=json.loads(sys.argv[2]); engine=Engine(store)
with engine.process_attempt(task):
    process=SupervisedProcess(
        [sys.executable,*sys.argv[3:]],identity=launch_identity(store.path,task),
        cwd=engine.root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,timeout=30)
    time.sleep(60)
"""


def wait_until(test, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            test.fail("Timed out waiting for the fixture boundary")
        time.sleep(0.02)


class CancellationTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self, kind="work", track="addition"):
        self.s.start(track)
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET kind=? WHERE track=?", (kind, track))
        task = self.s.claim("driver-" + track)
        engine = Engine(self.s)
        workspace = engine.ensure_workspace(task)
        return engine, task, workspace

    def writer(self, name):
        script = self.root / (name + ".py")
        script.write_text(WRITER)
        writes = self.root / (name + ".writes")
        release = self.root / (name + ".release")
        return [sys.executable, str(script), str(writes), str(release)], writes, release

    def attempt(self, task):
        return next(row for row in self.s.snapshot()["attempts"] if row["id"] == task["attempt"])

    def requests(self):
        with self.s.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE type='attempt.cancel_requested'"
                )
            ]

    def assert_cancelled(self, task, writes=None):
        attempt = self.attempt(task)
        self.assertEqual(attempt["status"], "cancelled")
        self.assertIsNotNone(attempt["finished"])
        self.assertIsNone(attempt["result"])
        ProcessBarrier(self.s.path, task["track"]).require_clear()
        if writes is not None:
            before = writes.read_bytes()
            time.sleep(0.15)
            self.assertEqual(writes.read_bytes(), before)

    def test_verification_cancel_stops_writes_before_attempt_finalization(self):
        engine, task, workspace = self.prepare("verify")
        argv, writes, release = self.writer("verification")
        engine.config.update(verify=argv, verify_timeout=20)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(engine.execute, task)
            try:
                wait_until(self, writes.exists)
                self.s.control("addition", "cancel")
                self.s.control("addition", "cancel")
                future.result(timeout=15)
            finally:
                release.touch()
        self.assert_cancelled(task, writes)
        self.assertIsNone(self.s.track("addition")["verification"])
        self.assertEqual(self.s.snapshot()["results"], [])
        self.assertEqual(len(self.requests()), 1)
        before = self.attempt(task)
        self.s.control("addition", "cancel")
        Engine(Store(self.s.path)).reconcile()
        self.assertEqual(self.attempt(task), before)

    def test_headless_cancel_preserves_other_track_and_pause_result(self):
        engine, task, workspace = self.prepare()
        argv, writes, release = self.writer("cancelled-worker")
        engine.config.update(worker={"type": "command", "argv": argv}, worker_timeout=20)
        self.s.register({**test_flow.DOC, "id": "second"})
        other_engine, other, other_workspace = self.prepare(track="second")
        other_argv, other_writes, other_release = self.writer("paused-worker")
        other_engine.config.update(
            worker={"type": "command", "argv": other_argv}, worker_timeout=20
        )
        with concurrent.futures.ThreadPoolExecutor() as pool:
            cancelled = pool.submit(engine.execute, task)
            continued = pool.submit(other_engine.execute, other)
            try:
                wait_until(self, lambda: writes.exists() and other_writes.exists())
                self.s.control("addition", "cancel")
                cancelled.result(timeout=15)
                self.assert_cancelled(task, writes)
                size = other_writes.stat().st_size
                wait_until(self, lambda: other_writes.stat().st_size > size)
                self.assertFalse(continued.done())
                self.s.control("second", "pause")
                other_release.touch()
                continued.result(timeout=15)
            finally:
                release.touch()
                other_release.touch()
        self.assertEqual(self.attempt(other)["status"], "finished")
        self.assertEqual(self.s.track("second")["control"], "paused")
        results = self.s.snapshot()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(json.loads(results[0]["body"])["summary"], "preserved")
        self.assertIsNone(self.s.claim("paused-followup"))
        self.s.control("second", "resume")
        self.assertEqual(self.s.claim("resumed")["track"], "second")

    def test_restart_after_cancel_before_delivery_waits_for_supervisor_proof(self):
        engine, task, workspace = self.prepare()
        argv, writes, release = self.writer("orphan")
        driver = subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(self.s.path), json.dumps(task), *argv[1:]],
            cwd=self.repo,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_until(self, writes.exists)
            self.s.control("addition", "cancel")
            reopened = Engine(Store(self.s.path))
            reopened.reconcile()
            self.assertEqual(self.attempt(task)["status"], "running")
            self.assertTrue(
                any(row["type"] == "attempt.cancel_blocked" for row in self.s.snapshot()["events"])
            )
            driver.kill()
            driver.wait(timeout=5)

            def recovered():
                reopened.reconcile()
                return self.attempt(task)["status"] == "cancelled"

            wait_until(self, recovered)
            self.assert_cancelled(task, writes)
            self.assertEqual(self.s.snapshot()["results"], [])
        finally:
            release.touch()
            if driver.poll() is None:
                driver.kill()
            driver.wait(timeout=5)

    def test_exit_before_finalization_is_recovered_once_with_lock_held_through_commit(self):
        engine, task, workspace = self.prepare()
        with engine.process_attempt(task):
            run(
                [sys.executable, "-c", "pass"],
                workspace,
                5,
                launch_identity=launch_identity(self.s.path, task),
            )
            self.s.control("addition", "cancel")
            Engine(Store(self.s.path)).reconcile()
            self.assertEqual(self.attempt(task)["status"], "running")
        request = self.requests()[0]
        transaction = self.s.transaction
        committed = []

        @contextmanager
        def observe_commit():
            with transaction() as connection:
                yield connection
            with self.assertRaises(Conflict):
                with file_lock(self.s.path / "locks" / "addition.lock"):
                    self.fail("Execution lock released before the cancellation commit")
            committed.append(True)

        with patch.object(self.s, "transaction", observe_commit):
            self.assertTrue(recover_request(self.s, request))
        self.assertEqual(committed, [True])
        before = self.attempt(task)
        self.assertFalse(recover_request(Store(self.s.path), request))
        self.assertEqual(self.attempt(task), before)
        self.assert_cancelled(task)

    def test_missing_inventory_never_confirms_cancellation(self):
        engine, task, workspace = self.prepare()
        self.s.control("addition", "cancel")
        engine.reconcile()
        self.assertEqual(self.attempt(task)["status"], "running")
        with self.assertRaises(ProcessBarrierError):
            ProcessBarrier(self.s.path, "addition").require_clear()
        before = self.s.snapshot()["attempts"]
        Engine(Store(self.s.path)).reconcile()
        self.assertEqual(self.s.snapshot()["attempts"], before)

    def test_consumed_launch_without_confirmation_keeps_attempt_running(self):
        engine, task, workspace = self.prepare()
        identity = launch_identity(self.s.path, task)
        gate = LaunchGate.prepare(**identity, backend="interrupted-fixture")
        ProcessInventory(self.s.path, "addition", task["attempt"]).prepared(identity["execution"])
        with gate.launching():
            pass  # Simulate interruption after dispatch but before any exit proof.
        self.s.control("addition", "cancel")
        Engine(Store(self.s.path)).reconcile()
        self.assertEqual(self.attempt(task)["status"], "running")
        with self.assertRaises(ProcessBarrierError):
            ProcessBarrier(self.s.path, "addition").require_clear()

    def test_old_result_cannot_affect_reassigned_generation(self):
        engine, task, workspace = self.prepare()
        engine.update(task, issue=123)
        engine.remote = Mock()
        replacement = []

        def late_result(*args):
            self.s.control("addition", "cancel")
            # Model reassignment of the same task after its old generation was fenced.
            with self.s.transaction() as connection:
                connection.execute(
                    "UPDATE tasks SET status='queued',owner=NULL,lease=NULL WHERE id=?",
                    (task["id"],),
                )
                connection.execute("UPDATE tracks SET control='active' WHERE id='addition'")
            replacement.append(self.s.claim("replacement"))
            return {
                "summary": "late",
                "changes": [{"path": "calc.py", "content": "late write"}],
                "verify": True,
                "publish": True,
            }

        original = (workspace / "calc.py").read_bytes()
        with (
            patch("todo_flow.engine.run_worker", side_effect=late_result),
            patch.object(engine, "verify") as verify,
            patch.object(engine, "publish") as publish,
        ):
            engine.execute(task)
        verify.assert_not_called()
        publish.assert_not_called()
        self.assertEqual((workspace / "calc.py").read_bytes(), original)
        self.assertGreater(replacement[0]["generation"], task["generation"])
        self.assert_cancelled(task)
        self.assertEqual(self.attempt(replacement[0])["status"], "running")
        before = self.s.track("addition")
        with self.assertRaises(Conflict):
            engine.record_review(
                task,
                {
                    "summary": "late review",
                    "verdict": "met",
                    "conditions": [{"id": "sum", "verdict": "met", "evidence": "late"}],
                },
            )
        with self.assertRaises(Conflict):
            engine.verify(task, workspace)
        with self.assertRaises(Conflict):
            self.s.finish(task, {"summary": "late completion"})
        self.assertEqual(self.s.track("addition"), before)
        self.assertEqual(self.s.snapshot()["results"], [])
        self.assertEqual(engine.remote.mock_calls, [])

    def test_cancellation_during_attempt_commit_can_retry_without_changing_identity(self):
        engine, task, workspace = self.prepare()
        inventory = ProcessInventory(
            self.s.path, "addition", task["attempt"], task["id"], task["generation"]
        )
        inventory.start()
        self.s.control("addition", "cancel")
        request = self.requests()[0]
        event = self.s.event

        def fail_receipt(connection, kind, track, value):
            if kind == "attempt.cancelled":
                raise OSError("injected commit interruption")
            return event(connection, kind, track, value)

        with patch.object(self.s, "event", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                recover_request(self.s, request)
        self.assertEqual(self.attempt(task)["status"], "running")
        self.assertEqual(inventory.read()["state"], "sealed")
        Engine(Store(self.s.path)).reconcile()
        self.assert_cancelled(task)


class NativeCancellationTests(unittest.TestCase):
    def test_supported_native_cancel_stops_real_server_writes(self):
        fixture = test_native_execution.NativeExecutionTests()
        fixture.setUp()
        try:
            fixture.prepare()
            writes = fixture.fixture / "native-writes"
            codex = fixture.bin / "codex"
            codex.write_text(
                codex.read_text().replace(
                    "if (root/'hang-after-accept').exists():time.sleep(60)",
                    "if (root/'hang-after-accept').exists():\n"
                    "   while True:\n"
                    "    with (root/'native-writes').open('a') as stream:stream.write('tick')\n"
                    "    time.sleep(.01)",
                )
            )
            (fixture.fixture / "hang-after-accept").touch()
            fixture.config["worker_timeout"] = 20
            fixture.engine.config = fixture.config
            # The adapter fixture supplies a real checkout and a preflight-only
            # receipt. Managed workspace creation/recovery is a separate boundary.
            with (
                patch("todo_flow.engine.managed_workspace.ensure", return_value=fixture.workspace),
                patch("todo_flow.worker.select_launcher", return_value=fixture.launcher),
                concurrent.futures.ThreadPoolExecutor() as pool,
            ):
                future = pool.submit(fixture.engine.execute, fixture.task)
                try:
                    wait_until(self, lambda: writes.exists() or future.done())
                    if future.done():
                        future.result()
                    self.assertTrue(writes.exists(), fixture.s.snapshot()["results"])
                finally:
                    fixture.s.control("addition", "cancel")
                    future.result(timeout=15)
            ProcessBarrier(fixture.s.path, "addition").require_clear()
            before = writes.read_bytes()
            time.sleep(0.15)
            self.assertEqual(writes.read_bytes(), before)
            snapshot = fixture.s.snapshot()
            self.assertEqual(snapshot["attempts"][0]["status"], "cancelled")
            self.assertEqual(snapshot["results"], [])
            folder = fixture.s.path / "attempts" / fixture.task["attempt"]
            self.assertFalse((folder / "native-proposal.json").exists())
        finally:
            fixture.doCleanups()
            fixture.tearDown()
