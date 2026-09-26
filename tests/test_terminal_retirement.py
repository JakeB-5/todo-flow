"""Synthetic physical inventory tests for the shared retirement boundary."""

from pathlib import Path
import tempfile
import unittest

from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_retirement import TerminalObservation, retire_terminal
from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots


class TerminalRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.inventory = ProcessInventory(self.root, "track", "attempt", "task", 1)
        self.inventory.start()
        self.physical = {}
        self.closes = []

    def launch(self, *, confirmed=True):
        identity = self.inventory.register()
        gate = LaunchGate.prepare(**identity, backend="synthetic")
        self.inventory.prepared(identity["execution"])
        owner = {
            "track": "track",
            "attempt": "attempt",
            "execution": identity["execution"],
            "task": "task",
            "generation": 1,
        }
        lease = self.slots.reserve(owner, "synthetic", evidence={"fixture": "launch"})
        resource = {"backend": "synthetic", "handle": identity["execution"]}
        lease = self.slots.transition(
            lease, "active", resource=resource, evidence={"fixture": "accepted"}
        )
        self.physical[resource["handle"]] = "idle"
        with gate.launching():
            pass
        if confirmed:
            event = gate._advance(gate._event(), "cleaning", "Synthetic exit", {"fixture": True})
            gate._advance(
                event,
                "confirmed",
                "Synthetic group exit",
                {
                    "identity": {key: identity[key] for key in ("track", "attempt", "execution")},
                    "outcome": "group-exited",
                    "proof": "Synthetic supervisor confirmed group exit",
                },
            )
        return lease

    def inspect(self, resource):
        status = self.physical.get(resource["handle"], "absent")
        return TerminalObservation(status, resource, "Complete synthetic inventory", "activity-1")

    def close(self, observation):
        history = self.slots.snapshot()["slots"].values()
        current = next(
            row["history"][-1]
            for row in history
            if row["history"][-1]["resource"] == observation.resource
        )
        self.assertEqual(current["state"], "closing")
        self.closes.append(observation.resource["handle"])
        del self.physical[observation.resource["handle"]]

    def retire(self, lease, *, close=None, inspect=None):
        return retire_terminal(
            self.slots,
            lease,
            inspect_resource=inspect or self.inspect,
            close_resource=close or self.close,
        )

    def test_fifty_retirements_preserve_history_and_bound_physical_inventory(self):
        maximum = 0
        for _ in range(25):
            first = self.launch()
            second = self.launch()
            maximum = max(maximum, len(self.physical))
            for lease in (first, second):
                self.assertEqual(self.retire(lease)["state"], "closed")
        snapshot = self.slots.snapshot()
        self.assertEqual(maximum, 2)
        self.assertEqual(snapshot["max_owned"], 2)
        self.assertEqual(len(self.closes), 50)
        self.assertEqual(len(self.physical), 0)
        for row in snapshot["slots"].values():
            self.assertEqual(
                [event["state"] for event in row["history"]],
                ["reserved", "active", "closing", "closed"],
            )
            self.assertTrue(row["history"][-1]["evidence"]["process_confirmation"])

    def test_lost_response_recovers_by_absence_without_duplicate_close(self):
        lease = self.launch()

        def lose_response(observation):
            self.close(observation)
            raise OSError("Synthetic response loss")

        with self.assertRaises(OSError):
            self.retire(lease, close=lose_response)
        self.slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.assertEqual(self.slots.counts(self.slots.snapshot())["closing"], 1)
        self.assertEqual(self.retire(lease)["state"], "closed")
        self.assertEqual(self.retire(lease)["state"], "closed")
        self.assertEqual(len(self.closes), 1)

    def test_restart_before_dispatch_does_not_replay_close(self):
        lease = self.launch()

        def crash_before_dispatch(observation):
            raise OSError("Synthetic crash before dispatch")

        with self.assertRaises(OSError):
            self.retire(lease, close=crash_before_dispatch)
        self.slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.assertEqual(self.retire(lease)["state"], "closing")
        self.assertEqual(len(self.physical), 1)
        self.assertEqual(self.closes, [])

    def test_success_response_without_physical_absence_keeps_capacity_charged(self):
        lease = self.launch()
        self.assertEqual(self.retire(lease, close=lambda observation: True)["state"], "closing")
        self.assertEqual(self.retire(lease)["state"], "closing")
        self.assertEqual(self.slots.counts(self.slots.snapshot())["closed"], 0)
        self.assertEqual(self.closes, [])

    def test_user_activity_and_unknown_resources_are_not_closed(self):
        for state in ("busy", "unknown"):
            lease = self.launch()
            self.physical[lease["resource"]["handle"]] = state
            self.assertEqual(self.retire(lease)["state"], "quarantined")
        self.assertEqual(self.closes, [])
        with self.assertRaises(TerminalCapacityError):
            self.launch()

    def test_activity_change_during_conditional_close_is_preserved(self):
        lease = self.launch()

        def refuse_changed_activity(observation):
            self.physical[observation.resource["handle"]] = "busy"
            return False

        self.assertEqual(self.retire(lease, close=refuse_changed_activity)["state"], "closing")
        self.assertEqual(self.retire(lease)["state"], "closing")
        self.assertEqual(len(self.physical), 1)
        self.assertEqual(self.closes, [])

    def test_changed_physical_identity_cannot_authorize_close(self):
        lease = self.launch()

        def changed_identity(resource):
            return TerminalObservation(
                "idle", {**resource, "handle": "user-resource"}, "Changed identity", "activity-1"
            )

        with self.assertRaises(TerminalCapacityError):
            self.retire(lease, inspect=changed_identity)
        self.assertEqual(self.slots.counts(self.slots.snapshot())["active"], 1)
        self.assertEqual(self.closes, [])

    def test_changed_claim_cannot_inspect_or_close_resource(self):
        lease = self.launch()
        with self.inventory.locked():
            value = self.inventory.read()
            value["claim"] = {"task": "replacement", "generation": 2}
            self.inventory.write(value)
        with self.assertRaises(ProcessBarrierError):
            self.retire(lease, inspect=lambda resource: self.fail("Unexpected physical probe"))
        self.assertEqual(self.closes, [])

    def test_live_execution_cannot_inspect_or_close_resource(self):
        lease = self.launch(confirmed=False)
        with self.assertRaises(TerminalCapacityError):
            self.retire(lease, inspect=lambda resource: self.fail("Unexpected physical probe"))
        self.assertEqual(self.closes, [])

    def test_sealed_inventory_can_reconcile_same_execution(self):
        lease = self.launch()
        self.inventory.seal()
        self.assertEqual(self.retire(lease)["state"], "closed")
