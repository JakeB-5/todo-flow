"""Capacity ledger tests; backend ownership and physical-tab tests are separate."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots


def owner(number, generation=1):
    return {
        "track": "synthetic",
        "attempt": f"attempt-{number}",
        "execution": f"execution-{number}",
        "task": f"task-{number}",
        "generation": generation,
    }


class TerminalSlotsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.slots = self.reopen()

    def reopen(self):
        return TerminalSlots(self.directory, concurrency=2, idle_limit=1)

    def reserve(self, number):
        return self.slots.reserve(
            owner(number), "synthetic", evidence={"launch_intent": str(number)}
        )

    def active(self, lease):
        return self.slots.transition(
            lease,
            "active",
            resource=lease["resource"] or {"handle": lease["slot"], "incarnation": "one"},
            evidence={"accepted": True},
        )

    def close(self, lease):
        closing = self.slots.transition(lease, "closing", evidence={"group_exit": True})
        self.slots = self.reopen()
        return self.slots.transition(closing, "closed", evidence={"physical_absence": True})

    def test_fifty_cycles_retain_one_idle_slot_and_bound_reservations(self):
        idle = self.active(self.reserve("idle"))
        self.slots.transition(idle, "idle", evidence={"idle_probe": True})
        for number in range(50):
            first = self.active(self.reserve(f"{number}-first"))
            second = self.active(self.reserve(f"{number}-second"))
            with self.assertRaises(TerminalCapacityError):
                self.reserve(f"{number}-overflow")
            self.close(first)
            self.close(second)
        snapshot = self.slots.snapshot()
        counts = self.slots.counts(snapshot)
        self.assertEqual(snapshot["max_owned"], 3)
        self.assertEqual(counts["closed"], 100)
        self.assertEqual(counts["idle"], 1)
        self.assertEqual(counts["active"], 0)

    def test_ten_fresh_executions_share_one_slot_with_distinct_evidence(self):
        lease = self.active(self.reserve(0))
        physical = lease["resource"]
        slot = lease["slot"]
        for number in range(1, 10):
            idle = self.slots.transition(lease, "idle", evidence={"exit": number - 1})
            self.slots = self.reopen()
            reserved = self.slots.checkout(idle, owner(number), evidence={"probe": number})
            with self.assertRaises(TerminalCapacityError):
                self.slots.checkout(idle, owner(f"duplicate-{number}"), evidence={"probe": True})
            lease = self.active(reserved)
            self.assertEqual(lease["slot"], slot)
            self.assertEqual(lease["resource"], physical)
        snapshot = self.slots.snapshot()
        self.assertEqual(snapshot["max_owned"], 1)
        history = snapshot["slots"][slot]["history"]
        self.assertEqual(len({e["owner"]["execution"] for e in history}), 10)
        self.assertEqual(history[0]["evidence"], {"launch_intent": "0"})

    def test_restart_never_reissues_an_uncertain_reservation(self):
        first = self.reserve(1)
        self.reserve(2)
        self.slots = self.reopen()
        with self.assertRaises(TerminalCapacityError):
            self.reserve(1)
        with self.assertRaises(TerminalCapacityError):
            self.reserve(3)
        quarantined = self.slots.transition(
            first, "quarantined", evidence={"reason": "create response lost"}
        )
        self.slots = self.reopen()
        with self.assertRaises(TerminalCapacityError):
            self.reserve(3)
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(quarantined, "closed", evidence={"guess": "absent"})
        self.assertEqual(len(self.slots.snapshot()["slots"]), 2)

    def test_interrupted_close_stays_charged_and_cannot_be_dispatched_twice(self):
        lease = self.active(self.reserve(1))
        closing = self.slots.transition(lease, "closing", evidence={"group_exit": True})
        self.slots = self.reopen()
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(lease, "closing", evidence={"retry": True})
        self.active(self.reserve(2))
        self.active(self.reserve(3))
        self.assertEqual(self.slots.snapshot()["max_owned"], 3)
        closed = self.slots.transition(closing, "closed", evidence={"physical_absence": True})
        self.slots = self.reopen()
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(closing, "closed", evidence={"duplicate": True})
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(closed, "active", evidence={"resurrection": True})

    def test_claim_and_physical_identity_changes_do_not_authorize_transition(self):
        lease = self.active(self.reserve(1))
        changed = {**lease, "owner": owner(1, generation=2)}
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(changed, "closing", evidence={"group_exit": True})
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(
                lease,
                "idle",
                resource={"handle": "other", "incarnation": "two"},
                evidence={"probe": True},
            )
        self.slots.transition(lease, "quarantined", evidence={"activity": "late output"})
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(lease, "idle", evidence={"stale_probe": True})

    def test_idle_limit_requires_retirement(self):
        first = self.active(self.reserve(1))
        second = self.active(self.reserve(2))
        self.slots.transition(first, "idle", evidence={"group_exit": True})
        with self.assertRaises(TerminalCapacityError):
            self.slots.transition(second, "idle", evidence={"group_exit": True})
        self.close(second)
        self.assertEqual(self.slots.counts(self.slots.snapshot())["idle"], 1)

    def test_competing_supervisors_share_capacity(self):
        def reserve(number):
            try:
                return self.reopen().reserve(
                    owner(number), "synthetic", evidence={"launch_intent": number}
                )
            except TerminalCapacityError:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(reserve, range(20)))
        self.assertEqual(sum(result is not None for result in results), 2)
        self.assertEqual(self.slots.snapshot()["max_owned"], 2)

    def test_closed_execution_cannot_be_reserved_again_with_a_new_claim(self):
        self.close(self.active(self.reserve(1)))
        with self.assertRaises(TerminalCapacityError):
            self.slots.reserve(owner(1, generation=2), "synthetic", evidence={"retry": True})

    def test_unknown_version_and_changed_limits_block_without_overwriting_evidence(self):
        self.reserve(1)
        raw = self.slots.path.read_text()
        changed = TerminalSlots(self.directory, concurrency=3, idle_limit=1)
        with self.assertRaises(TerminalCapacityError):
            changed.reserve(owner(2), "synthetic", evidence={"launch_intent": 2})
        self.assertEqual(self.slots.path.read_text(), raw)
        value = json.loads(raw)
        value["version"] = 999
        self.slots.path.write_text(json.dumps(value))
        with self.assertRaises(TerminalCapacityError):
            self.reserve(2)
        self.assertEqual(json.loads(self.slots.path.read_text())["version"], 999)

    def test_headless_has_no_slot(self):
        with self.assertRaises(ValueError):
            self.slots.reserve(owner(1), "headless", evidence={"launch_intent": 1})
        self.assertEqual(self.slots.snapshot()["slots"], {})
