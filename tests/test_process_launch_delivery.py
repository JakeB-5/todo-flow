import subprocess
import sys
import unittest

import test_flow
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate
from todo_flow.store import Store


DELIVERY = """
import subprocess
import sys
from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_launch import LaunchGate

directory, attempt, marker = sys.argv[1:]
# Dispatch has happened, but delivery is delayed until the parent releases stdin.
sys.stdin.readline()
gate = LaunchGate(directory, "addition", attempt, "execution")
try:
    with gate.launching():
        proc = subprocess.Popen([
            sys.executable, "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            marker,
        ], start_new_session=True)
        proc.wait(timeout=5)
except ProcessBarrierError:
    raise SystemExit(73)
raise SystemExit(0)
"""


class DelayedLaunchDeliveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self):
        self.s.start("addition")
        task = self.s.claim("driver")
        gate = LaunchGate.prepare(
            self.s.path, "addition", task["attempt"], "execution", backend="test"
        )
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        return task, gate

    def assert_delayed_delivery_rejected(self, task, before_delivery):
        marker = self.s.path / "unexpected-spawn"
        delivery = subprocess.Popen(
            [sys.executable, "-c", DELIVERY, str(self.s.path), task["attempt"], str(marker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            before_delivery()
            stdout, stderr = delivery.communicate("deliver\n", timeout=10)
            self.assertEqual(delivery.returncode, 73, stdout + stderr)
            self.assertFalse(marker.exists(), "Late delivery reached a real subprocess")
        finally:
            if delivery.poll() is None:
                delivery.kill()
            delivery.communicate(timeout=5)

    def test_cancelled_delivery_cannot_spawn_after_new_engine_recovery(self):
        task, gate = self.prepare()

        def recover():
            gate.cancel_pending()
            recovered = Engine(Store(self.s.path))
            recovered.process_barrier("addition").require_clear()
            recovered.reconcile()
            self.assertTrue(
                any(event["type"] == "claim.recovered" for event in self.s.snapshot()["events"])
            )

        self.assert_delayed_delivery_rejected(task, recover)
        Engine(Store(self.s.path)).process_barrier("addition").require_clear()

    def test_consumed_delivery_stays_blocked_after_driver_death_and_restart(self):
        task, gate = self.prepare()
        driver = """
import os
import sys
from todo_flow.process_launch import LaunchGate

gate = LaunchGate(sys.argv[1], "addition", sys.argv[2], "execution")
with gate.launching():
    os._exit(71)
"""
        result = subprocess.run(
            [sys.executable, "-c", driver, str(self.s.path), task["attempt"]],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 71, result.stderr)
        history = gate.barrier.history()
        before = self.s.snapshot()

        def recover():
            recovered = Engine(Store(self.s.path))
            with self.assertRaises(ProcessBarrierError):
                LaunchGate(self.s.path, "addition", task["attempt"], "execution").cancel_pending()
            recovered.reconcile()
            with self.assertRaises(ProcessBarrierError):
                recovered.process_barrier("addition").require_clear()
            after = self.s.snapshot()
            self.assertEqual(after["tasks"], before["tasks"])
            self.assertEqual(after["attempts"], before["attempts"])

        self.assert_delayed_delivery_rejected(task, recover)
        self.assertEqual(gate.barrier.history(), history)
