import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Store


class LaunchGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.gate = LaunchGate.prepare(
            self.directory, "track", "attempt", "execution", backend="test"
        )

    def reopen(self, attempt="attempt"):
        return LaunchGate(self.directory, "track", attempt, "execution")

    def test_cancel_prevents_late_and_duplicate_launches(self):
        self.reopen().cancel_pending()
        self.reopen().cancel_pending()
        self.reopen().barrier.require_clear()
        with self.assertRaises(ProcessBarrierError):
            with self.gate.launching():
                self.fail("Cancelled delivery reached spawn")
        self.assertEqual(self.gate.barrier.history()[-1]["evidence"]["outcome"], "not-spawned")

    def test_inflight_launch_cannot_be_cancelled_or_delivered_twice(self):
        with self.gate.launching():
            with self.assertRaises(ProcessBarrierError):
                self.reopen().cancel_pending()
            with self.assertRaises(ProcessBarrierError):
                with self.reopen().launching():
                    self.fail("Concurrent delivery reached spawn")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            with self.reopen().launching():
                self.fail("Duplicate delivery reached spawn")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().barrier.require_clear()

    def test_failed_spawn_remains_unknown_without_assuming_no_child(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.gate.launching():
                raise KeyboardInterrupt
        self.assertEqual(self.reopen().barrier.history()[-1]["state"], "unknown")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            self.reopen().barrier.require_clear()

    def test_failed_permit_persistence_never_reaches_spawn(self):
        with patch("todo_flow.process_launch.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(ProcessBarrierError):
                with self.gate.launching():
                    self.fail("Spawn preceded durable consumption")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            self.reopen().barrier.require_clear()

    def test_interrupted_cancellation_is_resumable_but_rechecks_durability(self):
        with patch.object(self.gate, "_advance", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.gate.cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            with self.reopen().launching():
                self.fail("Launch followed interrupted cancellation")
        with patch("todo_flow.process_launch.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(ProcessBarrierError):
                self.reopen().cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            self.reopen().barrier.require_clear()
        self.reopen().cancel_pending()
        self.reopen().barrier.require_clear()

    def test_wrong_identity_and_corrupt_permit_do_not_clear_intent(self):
        with self.assertRaises(ProcessBarrierError):
            self.reopen("other-attempt").cancel_pending()
        self.gate.path.write_bytes(b"la")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            with self.reopen().launching():
                self.fail("Corrupt permit reached spawn")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().barrier.require_clear()

    def test_legacy_intent_cannot_be_declared_unspawned(self):
        self.gate.cancel_pending()
        self.gate.barrier.begin(
            "legacy", "legacy-execution", reason="unknown old launch", evidence={"pid": 42}
        )
        legacy = LaunchGate(self.directory, "track", "legacy", "legacy-execution")
        with self.assertRaises(ProcessBarrierError):
            legacy.cancel_pending()
        with self.assertRaises(ProcessBarrierError):
            legacy.barrier.require_clear()


class LaunchGateRecoveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def interrupted_driver(self, stage):
        self.s.start("addition")
        task = self.s.claim("driver")
        gate = LaunchGate.prepare(
            self.s.path, "addition", task["attempt"], "execution", backend="test"
        )
        # The fixture child has no descendants and is reaped to avoid orphaning
        # test processes. The driver then dies without acknowledging spawn/exit.
        code = """
import os
import subprocess
import sys
from todo_flow.process_launch import LaunchGate

directory, attempt, stage = sys.argv[1:]
gate = LaunchGate(directory, "addition", attempt, "execution")
if stage == "intent":
    os._exit(71)
with gate.launching():
    if stage == "consumed":
        os._exit(71)
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc.wait(timeout=5)
    if stage == "spawned":
        os._exit(71)
if stage == "cleaning":
    event = gate.barrier.history()[-1]
    gate.barrier.advance(
        attempt, "execution", "cleaning", expected_revision=event["revision"],
        reason="Fixture interrupted before cleanup confirmation",
        evidence={"stage": stage},
    )
os._exit(71)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.s.path), task["attempt"], stage],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 71, result.stderr)
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        recovered = Engine(Store(self.s.path))
        before = self.s.snapshot()
        recovered.reconcile()
        self.assertEqual(before["tasks"], self.s.snapshot()["tasks"])
        self.assertEqual(before["attempts"], self.s.snapshot()["attempts"])
        with patch.object(recovered, "ensure_workspace") as workspace:
            recovered.execute(task)
        workspace.assert_not_called()
        with self.assertRaises(ProcessBarrierError):
            recovered.process_barrier("addition").require_clear()
        if stage == "intent":
            gate.cancel_pending()
            gate.barrier.require_clear()
        else:
            with self.assertRaises(ProcessBarrierError):
                gate.cancel_pending()

    def test_driver_dies_before_consuming_intent(self):
        self.interrupted_driver("intent")

    def test_driver_dies_after_consumption_before_spawn(self):
        self.interrupted_driver("consumed")

    def test_driver_dies_after_real_spawn_before_running_receipt(self):
        self.interrupted_driver("spawned")

    def test_driver_dies_with_running_receipt(self):
        self.interrupted_driver("running")

    def test_driver_dies_during_cleanup(self):
        self.interrupted_driver("cleaning")
