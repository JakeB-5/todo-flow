import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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
        self.tmp = tempfile.TemporaryDirectory()
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
        self.url = self.proc.stdout.readline().strip().split("Dashboard: ")[1]

    def tearDown(self):
        self.proc.terminate()
        self.proc.communicate(timeout=5)
        self.tmp.cleanup()

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
        for path in ["/", "/app.js", "/style.css"]:
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.status, 200)

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
