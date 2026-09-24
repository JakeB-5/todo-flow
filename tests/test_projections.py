import tempfile
import time
import unittest
from pathlib import Path

from todo_flow.projections import Dashboard
from todo_flow.store import Store, encode


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state")
        self.store.configure({"github": None, "base": "main", "endpoint": "review"})
        self.now = time.time()
        with self.store.transaction() as c:
            rows = []
            for i in range(2037):
                done = i >= 37
                doc = {
                    "id": f"track-{i:04}",
                    "title": f"작업 {i:04}",
                    "goal": "현재 목표",
                    "area": "editor",
                    "conditions": [],
                    "evidence": "HEAVY_DOCUMENT_SENTINEL" * 100,
                    "scope": "scope",
                }
                rows.append(
                    (
                        doc["id"],
                        1,
                        encode(doc),
                        "done" if done else "open",
                        "finished" if done else "idle",
                        encode({"output": "HEAVY_EVIDENCE_SENTINEL" * 1000}),
                        self.now - (i % 20),
                    )
                )
            c.executemany(
                "INSERT INTO tracks(id,revision,document,status,control,verification,updated) VALUES(?,?,?,?,?,?,?)",
                rows,
            )
            for i in range(80):
                self.store.event(
                    c,
                    "verification.recorded",
                    "track-0000",
                    {"output": "HUGE_EVENT_BODY" * 2000, "summary": f"event {i}"},
                )
        self.dashboard = Dashboard(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_excludes_completed_and_has_bounded_projection(self):
        r = self.dashboard.tracks()
        self.assertEqual(r["total"], 37)
        self.assertEqual(len(r["items"]), 25)
        self.assertTrue(all(x["status"] == "open" for x in r["items"]))
        body = encode(r)
        self.assertNotIn("HEAVY_", body)
        self.assertNotIn("document", body)
        self.assertLess(len(body), 30000)
        self.assertEqual(self.dashboard.overview()["counts"]["completed"], 2000)

    def test_completed_pagination_stable_on_equal_timestamps(self):
        seen = []
        as_of = None
        for offset in range(0, 2000, 100):
            r = self.dashboard.tracks(view="completed", limit=100, offset=offset, as_of=as_of)
            as_of = r["asOf"]
            seen.extend(x["id"] for x in r["items"])
        self.assertEqual(len(seen), 2000)
        self.assertEqual(len(set(seen)), 2000)
        with self.store.transaction() as c:
            c.execute(
                "UPDATE tracks SET status='done',control='finished',updated=? WHERE id='track-0000'",
                (time.time() + 1,),
            )
        pinned = self.dashboard.tracks(view="completed", as_of=as_of)
        self.assertEqual(pinned["total"], 2000)

    def test_search_is_server_side_literal_and_scope_specific(self):
        self.assertEqual(self.dashboard.tracks(view="completed", q="track-2036")["total"], 1)
        self.assertEqual(self.dashboard.tracks(q="track-2036")["total"], 0)
        self.assertEqual(self.dashboard.tracks(view="completed", q="%")["total"], 0)
        self.assertEqual(self.dashboard.tracks(view="completed", q="' OR 1=1 --")["total"], 0)
        self.assertEqual(
            self.dashboard.tracks(view="completed", sort="title")["items"][0]["id"], "track-0037"
        )

    def test_invalid_queries_and_page_clamping(self):
        for kwargs in (
            {"limit": 0},
            {"limit": 10000},
            {"offset": -1},
            {"view": "all"},
            {"sort": "bad"},
            {"as_of": "nan"},
        ):
            with self.assertRaises(ValueError):
                self.dashboard.tracks(**kwargs)
        r = self.dashboard.tracks(offset=1000)
        self.assertEqual(r["offset"], 25)
        self.assertEqual(len(r["items"]), 12)

    def test_detail_and_evidence_are_loaded_separately(self):
        d = self.dashboard.detail("track-2000")
        self.assertIn("HEAVY_DOCUMENT_SENTINEL", d["document"]["evidence"])
        self.assertNotIn("HEAVY_EVIDENCE_SENTINEL", encode(d))
        self.assertIn(
            "HEAVY_EVIDENCE_SENTINEL", encode(self.dashboard.evidence("track-2000", "verification"))
        )

    def test_event_cursor_omits_heavy_payload(self):
        one = self.dashboard.events(limit=25)
        two = self.dashboard.events(limit=25, before=one["next"])
        self.assertEqual(len(one["items"]), 25)
        self.assertFalse({x["seq"] for x in one["items"]} & {x["seq"] for x in two["items"]})
        self.assertNotIn("HUGE_EVENT_BODY", encode(one))
        self.assertEqual(one["items"][0]["summary"], "event 79")

    def test_expired_claim_is_not_counted_as_observed_running(self):
        self.store.start("track-0000")
        work = self.store.claim("worker")
        self.assertEqual(self.dashboard.overview()["counts"]["running"], 1)
        with self.store.transaction() as c:
            c.execute("UPDATE tasks SET lease=? WHERE id=?", (time.time() - 1, work["id"]))
        self.assertEqual(self.dashboard.overview()["counts"]["running"], 0)
        self.assertEqual(self.dashboard.tracks(control="running")["total"], 0)
        self.assertEqual(self.dashboard.activity()["items"][0]["status"], "running")
