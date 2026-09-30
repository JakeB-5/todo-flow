"""Cleanup remains local to an owned execution; unrelated workers are not gated."""

import json
from pathlib import Path
import tempfile
import unittest

from todo_flow.process_barrier import ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_retirement import (
    TerminalObservation,
    TerminalRetirementError,
    retire_terminal,
)


class TerminalRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.physical = {}
        self.closes = []
        self.number = 0

    def launch(self, *, confirmed=True):
        self.number += 1
        attempt = f"attempt-{self.number}"
        folder = self.root / attempt
        folder.mkdir()
        self.inventory = ProcessInventory(self.root, "track", attempt, "task", 1)
        self.inventory.start()
        identity = self.inventory.register()
        gate = LaunchGate.prepare(**identity, backend="synthetic")
        self.inventory.prepared(identity["execution"])
        owner = {k: identity[k] for k in ("track", "attempt", "execution")}
        owner.update(task="task", generation=1)
        resource = {"backend": "synthetic", "handle": identity["execution"]}
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
        return folder, owner, resource

    def inspect(self, resource):
        return TerminalObservation(
            self.physical.get(resource["handle"], "absent"),
            resource,
            "Complete synthetic inventory",
            "activity-1",
        )

    def close(self, observation):
        self.closes.append(observation.resource["handle"])
        del self.physical[observation.resource["handle"]]

    def retire(self, launch, *, close=None, inspect=None):
        return retire_terminal(
            *launch,
            self.root,
            inspect_resource=inspect or self.inspect,
            close_resource=close or self.close,
        )

    def test_fifty_retirements_close_owned_resources_without_capacity_ledger(self):
        for _ in range(50):
            launch = self.launch()
            self.assertEqual(self.retire(launch)["status"], "closed")
            intent = json.loads((launch[0] / "terminal-close-intent.json").read_text())
            self.assertEqual(intent["owner"], launch[1])
        self.assertEqual(len(self.closes), 50)
        self.assertEqual(self.physical, {})
        self.assertFalse((self.root / "terminal-slots.json").exists())

    def test_lost_close_response_recovers_by_absence_without_reclose(self):
        launch = self.launch()

        def lose_response(observation):
            self.close(observation)
            raise OSError("Synthetic response loss")

        with self.assertRaises(OSError):
            self.retire(launch, close=lose_response)
        self.assertEqual(self.retire(launch)["status"], "closed")
        self.assertEqual(len(self.closes), 1)

    def test_restart_before_dispatch_preserves_terminal_without_replaying_close(self):
        launch = self.launch()

        def fail(observation):
            raise OSError("Synthetic interruption before dispatch")

        with self.assertRaises(OSError):
            self.retire(launch, close=fail)
        self.assertEqual(self.retire(launch)["status"], "preserved")
        self.assertEqual(self.closes, [])
        # The preserved UI resource is unrelated to starting another execution.
        self.assertEqual(self.retire(self.launch())["status"], "closed")

    def test_busy_and_unknown_terminals_are_preserved_without_blocking_other_work(self):
        for state in ("busy", "unknown", "pending"):
            launch = self.launch()
            self.physical[launch[2]["handle"]] = state
            self.assertEqual(self.retire(launch)["status"], "preserved")
        self.assertEqual(self.closes, [])
        self.assertEqual(self.retire(self.launch())["status"], "closed")

    def test_success_response_without_absence_is_not_cleanup_success(self):
        launch = self.launch()
        self.assertEqual(self.retire(launch, close=lambda _: True)["status"], "preserved")
        self.assertEqual(self.retire(launch)["status"], "preserved")
        self.assertEqual(self.closes, [])

    def test_wrong_observed_resource_cannot_authorize_close(self):
        launch = self.launch()

        def foreign(resource):
            return TerminalObservation(
                "idle", {**resource, "handle": "user"}, "fixture", "activity"
            )

        with self.assertRaises(TerminalRetirementError):
            self.retire(launch, inspect=foreign)
        self.assertEqual(self.closes, [])

    def test_changed_claim_cannot_inspect_or_close(self):
        launch = self.launch()
        with self.inventory.locked():
            value = self.inventory.read()
            value["claim"] = {"task": "replacement", "generation": 2}
            self.inventory.write(value)
        with self.assertRaises(ProcessBarrierError):
            self.retire(launch, inspect=lambda _: self.fail("Unexpected probe"))

    def test_live_execution_cannot_inspect_or_close(self):
        launch = self.launch(confirmed=False)
        with self.assertRaises(TerminalRetirementError):
            self.retire(launch, inspect=lambda _: self.fail("Unexpected probe"))

    def test_sealed_inventory_can_clean_same_execution(self):
        launch = self.launch()
        self.inventory.seal()
        self.assertEqual(self.retire(launch)["status"], "closed")
