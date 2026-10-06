"""Read-only version diagnostics: separate sources, update plans and unchanged files."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from runtime_home import isolate_runtime_home
from todo_flow import skill_updates, version_diagnostics
from todo_flow.cli import main
from todo_flow.release import CONTRACTS, VERSION, release_number
from todo_flow.store import Store

CONSOLE = "[console_scripts]\ntodo-flow = todo_flow.cli:main\n"
CONSOLE += "trackrun = todo_flow.cli:trackrun\n"


def newer_version():
    major, minor, micro = release_number(VERSION)
    return f"{major}.{minor}.{micro + 1}"


class DashboardStub(BaseHTTPRequestHandler):
    status = 404
    body = {"error": "Not found"}

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps(self.body).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class VersionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        isolate_runtime_home(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        Store(self.state).configure(
            {
                "github": None,
                "base": "main",
                "endpoint": "review",
                "language": "en",
            }
        )
        self.bundle = self.root / "bundle"
        (self.bundle / "todo").mkdir(parents=True)
        (self.bundle / "todo/SKILL.md").write_text("Original instructions\n")
        self.target = self.root / "project/.agents/skills"
        # Plans assume a uv tool engine; the source-engine case patches this again.
        uv_tool = patch.object(version_diagnostics, "installation_kind", return_value="uv-tool")
        self.enterContext(uv_tool)

    def install(self, version=VERSION):
        return skill_updates.update(
            self.target,
            self.state,
            "en",
            install=True,
            source=self.bundle,
            version=version,
        )

    def wheel(self, name, contracts=CONTRACTS, skill=b"Original instructions\n"):
        path = self.root / name
        version = newer_version()
        info = f"todo_flow-{version}.dist-info/"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info + "METADATA", f"Name: todo-flow\nVersion: {version}\n")
            archive.writestr(info + "entry_points.txt", CONSOLE)
            archive.writestr("todo_flow/release.json", json.dumps(contracts))
            archive.writestr("todo_flow/skills/todo/SKILL.md", skill)
        return path

    def serve_stub(self, status, body):
        handler = type("Handler", (DashboardStub,), {"status": status, "body": body})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def snapshot(self, *roots):
        files = {}
        for root in roots:
            for path in sorted(root.rglob("*")):
                files[str(path)] = path.read_bytes() if path.is_file() else None
        return files

    def stop(self, proc):
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)

    def test_inventory_separates_process_path_cli_and_old_skill_versions(self):
        self.install(version="0.0.5")
        environment = self.root / "old-tool"
        metadata = environment / "lib/python3.11/site-packages/todo_flow-0.0.7.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: todo-flow\nVersion: 0.0.7\n")
        marker = self.root / "executed"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        script = bin_dir / "todo-flow"
        script.write_text(f"#!{environment}/bin/python\nopen({str(marker)!r}, 'w')\n")
        script.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(bin_dir)}):
            result = version_diagnostics.diagnose(self.state, self.target)
        found = {item["source"]: item for item in result["inventory"]}
        self.assertEqual(found["process"]["version"], VERSION)
        self.assertEqual(found["process"]["status"], "observed")
        self.assertEqual(found["path:todo-flow"]["version"], "0.0.7")
        self.assertEqual(found["path:todo-flow"]["status"], "observed")
        self.assertEqual(found["path:todo-flow"]["environment"], str(environment))
        self.assertEqual(found["path:trackrun"]["status"], "unknown")
        self.assertIsNone(found["path:trackrun"]["version"])
        self.assertEqual(found["skill:todo"]["version"], "0.0.5")
        self.assertEqual(found["skill:todo"]["skillProtocol"], 1)
        self.assertEqual(result["observedVersions"]["0.0.7"], ["path:todo-flow"])
        self.assertEqual(result["observedVersions"]["0.0.5"], ["skill:todo"])
        self.assertFalse(marker.exists())

    def test_unreachable_and_versionless_dashboards_are_not_observed(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = f"http://127.0.0.1:{probe.getsockname()[1]}"
        old = self.serve_stub(404, {"error": "Not found"})
        urls = [closed, old, "http://192.0.2.1:8765"]
        result = version_diagnostics.diagnose(self.state, dashboards=urls)
        dashboards = [item for item in result["inventory"] if item["source"] == "dashboard"]
        statuses = [item["status"] for item in dashboards]
        self.assertEqual(statuses, ["unreachable", "unknown", "unknown"])
        self.assertTrue(all(item["version"] is None for item in dashboards))
        self.assertIn("older dashboard", dashboards[1]["reason"])
        self.assertTrue(dashboards[1]["responding"])
        self.assertIn("Only", dashboards[2]["reason"])
        self.assertNotIn("responding", dashboards[0])

    def test_running_dashboard_reports_its_version_and_blocks_the_update(self):
        argv = [sys.executable, "-m", "todo_flow", "--state", str(self.state), "serve"]
        argv += ["--port", "0"]
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, proc)
        url = proc.stdout.readline().strip().split("Dashboard: ")[1]
        with urllib.request.urlopen(url + "/api/version") as response:
            body = json.load(response)
        self.assertEqual(body, {"version": VERSION, "contracts": CONTRACTS})
        wheel = self.wheel("valid.whl")
        result = version_diagnostics.diagnose(self.state, None, wheel, [url])
        dashboard = result["inventory"][-1]
        self.assertEqual((dashboard["status"], dashboard["version"]), ("observed", VERSION))
        self.assertEqual(result["plan"]["verdict"], "blocked")
        self.assertIn(url, result["plan"]["blocked"][0])

    def test_valid_and_unclear_release_manifests_have_different_verdicts(self):
        self.install()
        valid = self.wheel("valid.whl")
        unclear = self.wheel("unclear.whl", {**CONTRACTS, "manifest_version": 99})
        good = version_diagnostics.diagnose(self.state, self.target, valid)["plan"]
        bad = version_diagnostics.diagnose(self.state, self.target, unclear)["plan"]
        self.assertEqual(good["verdict"], "applicable")
        self.assertEqual(good["candidate"], newer_version())
        self.assertEqual((good["blocked"], good["conflicts"], good["unknown"]), ([], [], []))
        commands = [item["command"] for item in good["steps"] if item["command"]]
        self.assertEqual(commands[0], f"todo-flow upgrade --wheel {valid} --dry-run")
        self.assertTrue(any("update-skills" in command for command in commands))
        self.assertEqual(bad["verdict"], "unknown")
        self.assertIsNone(bad["candidate"])
        self.assertIn("manifest", bad["unknown"][0])
        with patch.object(version_diagnostics, "installation_kind", return_value="source"):
            source = version_diagnostics.diagnose(self.state, self.target, valid)["plan"]
        self.assertEqual(source["verdict"], "unknown")
        self.assertIn("not a uv tool", source["unknown"][0])

    def test_locally_modified_skill_conflicts_without_being_changed(self):
        self.install()
        (self.target / "todo/SKILL.md").write_text("My instructions\n")
        wheel = self.wheel("vendor.whl", skill=b"Vendor instructions\n")
        before = skill_updates.signature(self.target / "todo")
        plan = version_diagnostics.diagnose(self.state, self.target, wheel)["plan"]
        self.assertEqual(plan["verdict"], "conflict")
        self.assertIn("todo/SKILL.md", plan["conflicts"][0])
        self.assertEqual(skill_updates.signature(self.target / "todo"), before)

    def test_repeated_diagnostics_leave_state_skills_and_runtime_home_unchanged(self):
        self.install()
        home = Path(os.environ["TODO_FLOW_HOME"])
        wheel = self.wheel("valid.whl")
        old = self.serve_stub(404, {"error": "Not found"})
        roots = (self.state, self.target, home)
        before = self.snapshot(*roots)
        argv = ["--state", str(self.state), "diagnose-versions", "--target", str(self.target)]
        argv += ["--wheel", str(wheel), "--dashboard", old, "--json"]
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(argv)
            self.assertEqual(json.loads(output.getvalue())["plan"]["verdict"], "blocked")
            self.assertEqual(self.snapshot(*roots), before)
        absent = self.root / "absent-home"
        with patch.dict(os.environ, {"TODO_FLOW_HOME": str(absent)}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(argv[:-1])
        self.assertFalse(absent.exists())
        self.assertIn("(read-only)", output.getvalue())
        self.assertIn("BLOCKED", output.getvalue())
        self.assertEqual(self.snapshot(*roots), before)


if __name__ == "__main__":
    unittest.main()
