"""Validate conservative accounting in the host-only Orca measurement tool."""

import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "measure_orca_workers",
    Path(__file__).resolve().parents[1] / "scripts/measure_orca_workers.py",
)
MEASURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEASURE)


class OrcaMeasurementTests(unittest.TestCase):
    def payload(self):
        return {
            "ok": True,
            "_meta": {"runtimeId": "runtime"},
            "result": {
                "terminals": [
                    {
                        "executionHostId": "local",
                        "worktreeId": "worktree",
                        "tabId": "user-tab",
                        "ptyId": "user-pty",
                        "handle": "user-handle",
                    }
                ],
                "totalCount": 1,
                "truncated": False,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            },
        }

    def test_foreign_tabs_are_excluded_and_absent_owned_tabs_are_counted(self):
        present = MEASURE.inventory(self.payload(), "runtime", "worktree")
        owned = ("local", "worktree", "owned-tab", "owned-pty", "owned-handle")
        self.assertEqual(
            MEASURE.counts([owned], present),
            {"created": 1, "reused": 0, "reclaimed": 1, "preserved": 0, "foreign_present": 1},
        )
        self.assertEqual(MEASURE.counts([owned], present | {owned})["preserved"], 1)

    def test_incomplete_inventory_cannot_prove_absence(self):
        for field, value in (("truncated", True), ("totalCount", 2), ("hostScope", {})):
            with self.subTest(field=field):
                payload = self.payload()
                payload["result"][field] = value
                with self.assertRaises(ValueError):
                    MEASURE.inventory(payload, "runtime", "worktree")

    def test_runtime_or_identity_changes_cannot_prove_absence(self):
        with self.assertRaises(ValueError):
            MEASURE.inventory(self.payload(), "other-runtime", "worktree")
        payload = self.payload()
        del payload["result"]["terminals"][0]["ptyId"]
        with self.assertRaises(ValueError):
            MEASURE.inventory(payload, "runtime", "worktree")

    def test_duplicate_inventory_rows_are_rejected(self):
        payload = self.payload()
        payload["result"]["terminals"] *= 2
        payload["result"]["totalCount"] = 2
        with self.assertRaises(ValueError):
            MEASURE.inventory(payload, "runtime", "worktree")


if __name__ == "__main__":
    unittest.main()
