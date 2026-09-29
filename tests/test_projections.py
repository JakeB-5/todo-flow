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
                    "area": "workspace",
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

    def test_activity_groups_three_tasks_without_shipping_originals(self):
        purpose = "Fix verification: " + "검증 실패 😀\n" * 800
        with self.store.transaction() as c:
            c.executemany(
                "INSERT INTO tasks(id,track,kind,purpose,status,lease,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [
                    ("one", "track-0000", "work", purpose, "running", self.now + 60, 1, 2),
                    ("two", "track-0000", "review", purpose, "queued", None, 2, 2),
                    ("three", "track-0001", "work", purpose, "waiting", None, 3, 3),
                ],
            )
            c.execute(
                "UPDATE tracks SET verification=? WHERE id='track-0000'",
                (encode({"ok": False, "at": 1, "output": purpose}),),
            )
            c.execute(
                "INSERT INTO decisions(id,track,task,question,status,created)"
                " VALUES('decision','track-0001','three','Choose a policy','open',1)"
            )
        before = {
            str(p): p.read_bytes() for p in self.store.path.rglob("*.json") if p.is_file()
        }
        result = self.dashboard.activity()
        self.assertEqual((result["total"], result["taskTotal"]), (2, 3))
        first, second = result["items"]
        self.assertEqual(first["taskCount"], 2)
        self.assertEqual((first["running"], first["queued"], first["waiting"]), (1, 1, 0))
        self.assertEqual(first["current"]["id"], "one")
        self.assertEqual(first["current"]["intent"], "verification-repair")
        self.assertEqual(first["current"]["kind"], "work")
        self.assertEqual(first["verificationOk"], 0)
        self.assertEqual((second["waiting"], second["decisions"]), (1, 1))
        self.assertEqual(self.dashboard.decisions(track="track-0000")["total"], 0)
        self.assertEqual(self.dashboard.decisions(track="track-0001")["total"], 1)
        one = self.dashboard.activity_tasks("track-0000", limit=1)
        two = self.dashboard.activity_tasks("track-0000", limit=1, offset=1)
        self.assertEqual([one["items"][0]["id"], two["items"][0]["id"]], ["one", "two"])
        for response in (result, one, two):
            self.assertNotIn("검증 실패", encode(response))
            self.assertNotIn("purpose", encode(response))
            self.assertNotIn("HEAVY_", encode(response))
        self.assertEqual(self.dashboard.task("one")["task"]["purpose"], purpose)
        self.assertEqual(
            self.dashboard.evidence("track-0000", "verification")["value"]["output"],
            purpose,
        )
        after = {
            str(p): p.read_bytes() for p in self.store.path.rglob("*.json") if p.is_file()
        }
        self.assertEqual(before, after)

    def test_activity_pages_keep_groups_and_decision_only_tracks(self):
        with self.store.transaction() as c:
            c.executemany(
                "INSERT INTO tasks(id,track,kind,purpose,status,lease,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        f"task-{i}-{j}",
                        f"track-{i:04}",
                        "work",
                        "LONG_ORIGINAL_" * 1000,
                        "queued" if j else "running",
                        None if i == 0 else self.now + 60,
                        1,
                        1,
                    )
                    for i in range(36)
                    for j in range(4)
                ],
            )
            c.execute(
                "INSERT INTO decisions(id,track,question,status,created)"
                " VALUES('decision-only','track-0036','Choose','open',1)"
            )
            c.execute(
                "INSERT INTO tasks(id,track,kind,purpose,status,created,updated)"
                " VALUES('archived','track-0037','work','Ignore completed','queued',1,1)"
            )
        with self.store.connect() as c:
            before = [tuple(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
        seen = []
        for offset in range(0, 37, 7):
            result = self.dashboard.activity(limit=7, offset=offset)
            self.assertEqual((result["total"], result["taskTotal"]), (37, 144))
            self.assertLessEqual(len(result["items"]), 7)
            self.assertNotIn("LONG_ORIGINAL", encode(result))
            self.assertLess(len(encode(result)), 10000)
            seen.extend(t["id"] for t in result["items"])
        self.assertEqual(seen, [f"track-{i:04}" for i in range(37)])
        first = self.dashboard.activity(limit=1)["items"][0]
        self.assertEqual((first["status"], first["uncertain"]), ("running", 1))
        self.assertIsNone(first["current"]["intent"])
        last = self.dashboard.activity(limit=1, offset=36)["items"][0]
        self.assertEqual((last["taskCount"], last["decisions"], last["current"]), (0, 1, None))
        with self.store.connect() as c:
            self.assertEqual(
                before, [tuple(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
            )
        for query in (
            lambda: self.dashboard.activity(limit=101),
            lambda: self.dashboard.activity_tasks("track-0000", offset=-1),
        ):
            with self.assertRaises(ValueError):
                query()

    def test_expired_claim_is_not_counted_as_observed_running(self):
        self.store.start("track-0000")
        work = self.store.claim("worker")
        self.assertEqual(self.dashboard.overview()["counts"]["running"], 1)
        with self.store.transaction() as c:
            c.execute("UPDATE tasks SET lease=? WHERE id=?", (time.time() - 1, work["id"]))
        self.assertEqual(self.dashboard.overview()["counts"]["running"], 0)
        self.assertEqual(self.dashboard.tracks(control="running")["total"], 0)
        self.assertEqual(self.dashboard.activity()["items"][0]["status"], "running")
        self.assertEqual(self.dashboard.activity()["items"][0]["uncertain"], 1)
