import json
import tempfile
import time
import unittest
from pathlib import Path

from todo_flow.projections import Dashboard, planning_summary
from todo_flow.store import Store, encode


def role(provider, model, effort, basis):
    return {"provider": provider, "model": model, "effort": effort, "basis": basis}


# planning-summary-visible / planning-missing-and-bounded: expectations come from the
# authored document fields, never from routing, worker selection or provider confirmation.
DOCUMENTS = {
    "planned": {
        "effort": {"estimate": "medium - list, display, guidance", "basis": "Reuses metadata"},
        "workerPlan": {
            "version": 1,
            "roles": {
                "review": role("claude", "claude-opus-4-6", "high", "Fresh review"),
                "work": role("codex", "gpt-6.1-sol", "medium", "Known path"),
                "triage": role("codex", None, None, "Accept provider defaults"),
            },
        },
    },
    "legacy": {"effort": "small"},
    "partial": {
        "workerPlan": {
            "version": 1,
            "roles": {"work": {"provider": "codex", "model": "m" * 5000, "basis": "b" * 5000}},
        },
    },
    "malformed": {"effort": 3, "workerPlan": {"version": 1, "roles": "work"}},
    "absent": {},
}


class PlanningProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.configure({"github": None, "base": "main", "endpoint": "review"})
        now = time.time()
        with self.store.transaction() as c:
            for index, (id_, fields) in enumerate(DOCUMENTS.items()):
                doc = {"id": id_, "title": id_, "goal": "goal", "conditions": [], **fields}
                c.execute(
                    "INSERT INTO tracks(id,revision,document,updated) VALUES(?,?,?,?)",
                    (id_, 1, encode(doc), now - index),
                )
        self.dashboard = Dashboard(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        with self.store.connect() as c:
            tracks = [tuple(r) for r in c.execute("SELECT * FROM tracks ORDER BY id")]
            events = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return tracks, events

    def listed(self):
        return {t["id"]: t["planning"] for t in self.dashboard.tracks(limit=100)["items"]}

    def test_work_size_and_work_role_are_separate_from_other_roles(self):
        planned = self.listed()["planned"]
        self.assertEqual(
            planned["effort"],
            {"estimate": "medium - list, display, guidance", "basis": "Reuses metadata"},
        )
        self.assertEqual(list(planned["roles"]), ["work", "review", "triage"])
        self.assertEqual(
            planned["roles"]["work"],
            {
                "provider": "codex",
                "model": {"status": "authored", "value": "gpt-6.1-sol"},
                "effort": {"status": "authored", "value": "medium"},
                "basis": "Known path",
            },
        )
        review = planned["roles"]["review"]
        self.assertEqual(review["provider"], "claude")
        self.assertEqual(review["model"], {"status": "authored", "value": "claude-opus-4-6"})
        self.assertEqual(review["effort"], {"status": "authored", "value": "high"})
        self.assertEqual(planned["roles"]["triage"]["model"], {"status": "default"})
        self.assertEqual(planned["roles"]["triage"]["effort"], {"status": "default"})

    def test_missing_legacy_and_malformed_values_are_not_guessed(self):
        listed = self.listed()
        legacy = listed["legacy"]
        self.assertEqual(legacy["effort"], {"estimate": "small", "basis": None})
        self.assertIsNone(legacy["roles"])
        self.assertEqual(listed["absent"], {"effort": None, "roles": None})
        self.assertEqual(listed["malformed"], {"effort": None, "roles": {}})
        partial = listed["partial"]
        self.assertIsNone(partial["effort"])
        work = partial["roles"]["work"]
        self.assertEqual(work["effort"], {"status": "missing"})
        self.assertEqual(work["model"]["status"], "authored")
        self.assertEqual(len(work["model"]["value"]), 300)
        self.assertTrue(work["model"]["value"].endswith("…"))
        self.assertEqual(len(work["basis"]), 300)

    def test_listing_is_read_only_and_bounded(self):
        before = self.snapshot()
        body = encode(self.dashboard.tracks(limit=100))
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("m" * 400, body)
        self.assertNotIn("b" * 400, body)
        self.assertNotIn('"document"', body)
        self.assertNotIn("planning_roles", body)

    def test_parser_ignores_unknown_roles_and_unreadable_values(self):
        raw = json.dumps({"work": {"provider": "codex", "model": None, "basis": "b"}, "x": {}})
        self.assertEqual(
            planning_summary('"medium"', "object", raw),
            {
                "effort": {"estimate": "medium", "basis": None},
                "roles": {
                    "work": {
                        "provider": "codex",
                        "model": {"status": "default"},
                        "effort": {"status": "missing"},
                        "basis": "b",
                    },
                },
            },
        )
        self.assertEqual(planning_summary("{", "object", "{"), {"effort": None, "roles": {}})
        self.assertEqual(planning_summary(None, "null", None), {"effort": None, "roles": None})


if __name__ == "__main__":
    unittest.main()
