import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from runtime_home import isolate_runtime_home
from todo_flow.store import Store

DOC = {
    "id": "example",
    "title": "Example",
    "goal": "A result",
    "scope": "A file",
    "evidence": "User request",
    "conditions": [{"id": "ok", "text": "works", "method": "test"}],
}


class HttpTests(unittest.TestCase):
    def setUp(self):
        isolate_runtime_home(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = Store(Path(self.tmp.name) / "state")
        self.s.configure(
            {
                "github": None,
                "base": "main",
                "endpoint": "review",
                "demo": self._testMethodName == "test_synthetic_demo_rejects_execution",
            }
        )
        self.s.register(DOC)
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "todo_flow",
                "--state",
                str(self.s.path),
                "serve",
                "--port",
                "0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_server)
        self.url = self.proc.stdout.readline().strip().split("Dashboard: ")[1]

    def stop_server(self):
        self.proc.terminate()
        try:
            self.proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.communicate(timeout=5)

    def tearDown(self):
        self.doCleanups()

    def get(self, path="/api/state"):
        with urllib.request.urlopen(self.url + path) as r:
            return json.load(r)

    def post(self, path, data, token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Todo-Flow"] = token
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.url + "/api/" + path, json.dumps(data).encode(), headers)
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def test_read_only_authoring_and_csrf(self):
        snap = self.get()
        self.assertEqual(len(snap["tracks"]), 1)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post("start", {"tracks": ["example"]})
        self.assertEqual(err.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post("register", DOC, snap["token"])
        self.assertEqual(err.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post(
                "control",
                {"track": "example", "action": "pause"},
                snap["token"],
                "https://evil.invalid",
            )
        self.assertEqual(err.exception.code, 403)

    def test_no_dashboard_execution_and_decision_answer(self):
        token = self.get()["token"]
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post("start", {"tracks": ["example"]}, token)
        self.assertEqual(err.exception.code, 404)
        self.s.start("example")
        t = self.s.claim("worker")
        self.s.finish(t, {"summary": "Need choice", "question": "Which option?"})
        decision = self.s.snapshot()["decisions"][0]["id"]
        out = self.post("answer", {"decision": decision, "answer": "Option A"}, token)
        self.assertEqual(out["answered"], decision)
        self.assertEqual(self.s.claim("new")["kind"], "assess")

    def test_pause_resume_and_static_assets(self):
        token = self.get()["token"]
        self.s.start("example")
        self.post("control", {"track": "example", "action": "pause"}, token)
        self.assertIsNone(self.s.claim("worker"))
        self.post("control", {"track": "example", "action": "resume"}, token)
        self.assertIsNotNone(self.s.claim("worker"))
        for path in ["/", "/app.js", "/i18n.js", "/style.css"]:
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.status, 200)

    def test_worker_evidence_routes_are_attempt_scoped_bounded_and_read_only(self):
        self.s.register({**DOC, "id": "second", "title": "Second"})
        with self.s.transaction() as c:
            c.executemany(
                "INSERT INTO tasks(id,track,kind,purpose,status,lease,created,updated)"
                " VALUES(?,?,'work','Synthetic task','running',3000000000,1,1)",
                [("one", "example"), ("two", "second")],
            )
            c.executemany(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,'running')",
                [("a-old", "one", 1), ("a-new", "one", 2), ("b-one", "two", 1)],
            )
        for attempt, model, effort in (
            ("a-old", "old-model", "low"),
            ("a-new", "selected-a", "high"),
            ("b-one", "selected-b", None),
        ):
            folder = self.s.path / "attempts" / attempt
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "worker-selection.json").write_text(
                json.dumps(
                    {
                        "selected": {"provider": "codex", "model": model, "effort": effort},
                        "command": "PRIVATE_COMMAND",
                    }
                )
            )
        native = self.s.path / "attempts" / "a-new" / "native-session.json"
        native.write_text(json.dumps({"provider_confirmed": {"effort": "medium"}}))

        def snapshot():
            with self.s.connect() as c:
                rows = {
                    table: [tuple(row) for row in c.execute("SELECT * FROM " + table)]
                    for table in ("tracks", "tasks", "attempts", "events")
                }
            files = {
                str(path): path.read_bytes()
                for path in self.s.path.rglob("*.json")
                if path.is_file()
            }
            return rows, files

        before = snapshot()
        first = self.get("/api/activity?limit=1")
        second = self.get("/api/activity?limit=1&offset=1")
        self.assertEqual(first["total"], 2)
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["hasMore"])
        self.assertFalse(second["hasMore"])
        one = self.get("/api/tasks/one")
        two = self.get("/api/tasks/two")
        self.assertEqual(first["items"][0]["current"]["worker"], one["worker"])
        self.assertEqual(second["items"][0]["current"]["worker"], two["worker"])
        self.assertEqual(one["worker"]["attempt"], one["attempt"]["id"])
        self.assertEqual(one["worker"]["attempt"], "a-new")
        self.assertEqual(one["worker"]["model"]["selected"]["value"], "selected-a")
        self.assertEqual(one["worker"]["model"]["confirmed"]["status"], "unconfirmed")
        self.assertEqual(one["worker"]["effort"]["confirmed"]["value"], "medium")
        self.assertEqual(two["worker"]["model"]["selected"]["value"], "selected-b")
        self.assertEqual(two["worker"]["effort"]["selected"]["status"], "delegated")
        self.assertNotIn("PRIVATE_COMMAND", json.dumps([first, second, one, two]))
        self.assertNotIn("old-model", json.dumps([first, second, one, two]))
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.get("/api/activity?limit=101")
        self.assertEqual(err.exception.code, 400)
        self.assertEqual(before, snapshot())

    def test_bounded_routes_and_archive_separation(self):
        for i in range(30):
            self.s.register({**DOC, "id": f"track-{i}", "title": f"Track {i}"})
        with self.s.transaction() as c:
            c.execute("UPDATE tracks SET status='done',control='finished' WHERE id='example'")
        state = self.get()
        self.assertTrue(state["bounded"])
        self.assertEqual(len(state["tracks"]), 25)
        self.assertNotIn("document", state["tracks"][0])
        self.assertEqual(state["counts"]["completed"], 1)
        archive = self.get("/api/tracks?view=completed")
        self.assertEqual([x["id"] for x in archive["items"]], ["example"])
        self.assertEqual(len(self.get("/api/tracks?offset=25")["items"]), 5)
        detail = self.get("/api/tracks/example")
        self.assertEqual(detail["document"]["goal"], DOC["goal"])
        self.assertIsNone(self.get("/api/tracks/example/evidence/review")["value"])
        for query in ("limit=101", "limit=bad", "offset=-1", "view=all", "unknown=x"):
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.get("/api/tracks?" + query)
            self.assertEqual(err.exception.code, 400)

    def test_synthetic_demo_rejects_execution(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post("start", {"tracks": ["example"]}, self.get()["token"])
        self.assertEqual(err.exception.code, 409)
        self.assertEqual(self.s.track("example")["control"], "idle")

    def test_rich_document_route_is_isolated_and_assets_are_preserved(self):
        from todo_flow import documents

        source = Path(self.tmp.name) / "plan.html"
        html = (
            '<html><script type="application/json" id="todo-flow-track">'
            + json.dumps(DOC)
            + '</script><svg id="figure"></svg><script type="module" src="assets/view.js"></script></html>'
        )
        source.write_text(html)
        bundle = Path(self.tmp.name) / "assets"
        bundle.mkdir()
        (bundle / "view.js").write_text("window.documentLoaded=true;")
        self.s.register(documents.load(source, bundle), expected=1)
        detail = self.get("/api/tracks/example")
        self.assertNotIn("presentation", detail["document"])
        with urllib.request.urlopen(self.url + detail["documentView"]["url"]) as response:
            self.assertEqual(response.read().decode(), html)
            self.assertEqual(
                response.headers["Content-Security-Policy"], "sandbox allow-scripts allow-downloads"
            )
        with urllib.request.urlopen(self.url + "/documents/example/2/assets/view.js") as response:
            self.assertIn(b"documentLoaded", response.read())
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        for path in [
            "/documents/example/2/assets/%2e%2e/config/1.json",
            "/documents/example/999/index.html",
        ]:
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(self.url + path)
            self.assertEqual(err.exception.code, 404)
