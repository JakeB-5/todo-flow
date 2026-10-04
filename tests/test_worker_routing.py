"""Request isolation and real adapter launch arguments, with synthetic provider output."""

import concurrent.futures
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_home import isolate_runtime_home
from todo_flow import documents
from todo_flow.cli import main
from todo_flow.process_inventory import ProcessInventory
from todo_flow.store import Conflict, Store
from todo_flow.supervised_process import SupervisedProcess
from todo_flow.worker import run_worker
from todo_flow.worker_routing import ROLES, apply, request, resolve

DOC = {
    "id": "routing",
    "title": "Route workers",
    "goal": "Keep request constraints",
    "scope": "Worker selection",
    "evidence": "User selects a provider boundary",
    "conditions": [{"id": "route", "text": "Respect selection", "method": "Fake launch"}],
}


def choice(provider, effort="medium"):
    return {
        "provider": provider,
        "model": "gpt-6.1-sol" if provider == "codex" else "claude-sonnet-4-6",
        "effort": effort,
        "basis": "Synthetic role selection",
    }


class WorkerRoutingTests(unittest.TestCase):
    def setUp(self):
        isolate_runtime_home(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state")
        self.config = {
            "worker": {"type": "codex", "model": "gpt-6.1-sol", "effort": "medium"},
            "worker_profiles": {"claude": {"model": "claude-sonnet-4-6", "effort": "high"}},
            "worker_protocol": 2,
            "worker_launcher": "headless",
        }
        self.store.configure(self.config)
        self.mixed = {
            role: choice("codex" if index % 2 == 0 else "claude")
            for index, role in enumerate(ROLES)
        }
        self.doc = {**DOC, "workerPlan": {"version": 1, "roles": self.mixed}}
        self.store.register(self.doc)

    def test_registration_and_packaged_templates_roundtrip(self):
        root = Path(__file__).resolve().parents[1]
        for suffix in ("html", "md", "json"):
            with self.subTest(suffix=suffix):
                doc = documents.load(root / "templates" / ("track." + suffix))
                state = Store(self.root / suffix)
                state.register(doc)
                loaded = json.loads(state.track(doc["id"])["document"])
                self.assertEqual(loaded["workerPlan"], doc["workerPlan"])
                self.assertEqual(set(loaded["workerPlan"]["roles"]), set(ROLES) - {"watch"})
                self.assertIn("estimate", loaded["effort"])
        revised = {**self.doc, "workerPlan": {"version": 1, "roles": {"watch": choice("claude")}}}
        self.store.register(revised, expected=1)
        reopened = Store(self.store.path)
        self.assertEqual(
            json.loads(reopened.track("routing")["document"])["workerPlan"], revised["workerPlan"]
        )
        with self.assertRaises(ValueError):
            reopened.register({**revised, "workerPlan": {"version": 2, "roles": {}}}, expected=2)

    def test_request_only_restart_resume_and_explicit_reselection(self):
        roles_path = self.root / "roles.json"
        roles_path.write_text(json.dumps({"assess": choice("claude", "high")}))
        with contextlib.redirect_stdout(io.StringIO()), patch("todo_flow.cli.Engine") as engine:
            main(
                [
                    "--state",
                    str(self.store.path),
                    "trackrun",
                    "routing",
                    "--request-only",
                    "--worker-mode",
                    "claude-only",
                    "--worker-roles",
                    str(roles_path),
                ]
            )
        engine.assert_not_called()
        self.assertEqual(self.store.snapshot()["attempts"], [])
        self.store.control("routing", "pause")
        shutil.rmtree(self.store.path / ".cache")
        self.store = Store(self.store.path)
        self.store.control("routing", "resume")
        old = self.store.claim("first")
        selected = old["worker_selection"]
        self.assertEqual(selected["mode"], "claude-only")
        self.assertEqual(selected["source"], "request")
        original_events = [
            event for event in self.store.snapshot()["events"] if event["type"] == "worker.claimed"
        ]
        self.store.start("routing", worker_mode="codex-only", worker_roles={})
        self.assertEqual(old["worker_selection"], selected)
        self.assertEqual(
            [
                event
                for event in self.store.snapshot()["events"]
                if event["type"] == "worker.claimed"
            ],
            original_events,
        )
        self.store.finish(
            old, {"summary": "Continue", "next": [{"kind": "work", "purpose": "Next"}]}
        )
        self.store = Store(self.store.path)
        self.store.start("routing")
        new = self.store.claim("second")
        self.assertEqual(new["worker_selection"]["mode"], "codex-only")
        self.assertEqual(new["worker_selection"]["selected"]["provider"], "codex")
        self.assertEqual(self.store.config(), self.config)
        with self.assertRaises(Conflict):
            self.store.heartbeat(old)

    def test_simultaneous_requests_with_same_id_are_isolated(self):
        self.store.register({**self.doc, "id": "other"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.store.start, track, "shared", worker_mode=mode)
                for track, mode in (("routing", "codex-only"), ("other", "claude-only"))
            ]
            for future in futures:
                future.result()
        first = self.store.claim("one")
        second = self.store.claim("two")
        selections = {task["track"]: task["worker_selection"] for task in (first, second)}
        self.assertEqual(selections["routing"]["selected"]["provider"], "codex")
        self.assertEqual(selections["other"]["selected"]["provider"], "claude")
        self.assertEqual(self.store.config(), self.config)

    def test_all_roles_repair_and_retry_obey_actual_launch_boundary(self):
        for mode in ("auto", "codex-only", "claude-only"):
            track = mode
            self.store.register({**self.doc, "id": track})
            self.store.start(track, worker_mode=mode)
            calls = []
            kinds = ("assess", "work", "review", "triage", "work", "work", "watch")
            retry = None
            for index, kind in enumerate(kinds):
                task = self.store.claim("adapter")
                self.assertEqual(task["kind"], kind)
                if retry is not None:
                    self.assertEqual(task["id"], retry["id"])
                    self.assertNotEqual(task["attempt"], retry["attempt"])
                    with self.assertRaises(Conflict):
                        self.store.heartbeat(retry)
                    retry = None
                selected = task["worker_selection"]["selected"]

                def spawn(argv, **kwargs):
                    calls.append(argv[0])
                    self.assertEqual(argv[argv.index("--model") + 1], selected["model"])
                    if argv[0] == "codex":
                        self.assertIn(
                            "model_reasoning_effort=" + json.dumps(selected["effort"]), argv
                        )
                        self.assertIn("read-only", argv)
                        Path(argv[argv.index("--output-last-message") + 1]).write_text(
                            json.dumps({"summary": "Synthetic proposal"})
                        )
                    else:
                        self.assertEqual(argv[argv.index("--effort") + 1], selected["effort"])
                        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep")
                        kwargs["stdout"].write(
                            json.dumps(
                                {"type": "result", "structured_output": {"summary": "Synthetic"}}
                            )
                            + "\n"
                        )
                        kwargs["stdout"].flush()
                    return SupervisedProcess([sys.executable, "-c", "pass"], **kwargs)

                with (
                    ProcessInventory(
                        self.store.path, track, task["attempt"], task["id"], task["generation"]
                    ).lifecycle(),
                    patch("todo_flow.worker.SupervisedProcess", spawn),
                ):
                    run_worker(
                        self.config,
                        {"workspace": str(self.root)},
                        task,
                        self.store.path,
                        lambda pid: self.store.heartbeat(task, pid),
                    )
                receipt = json.loads(
                    (
                        self.store.path / "attempts" / task["attempt"] / "worker-selection.json"
                    ).read_text()
                )
                self.assertEqual(receipt["selected"], selected)
                self.assertIsNone(receipt["provider_confirmed"])
                if index == 4:
                    # Retry the same work obligation on a fresh attempt after a known exit.
                    retry = task
                    with self.store.transaction() as c:
                        c.execute(
                            "UPDATE attempts SET status='failed' WHERE id=?", (task["attempt"],)
                        )
                        c.execute("UPDATE tasks SET status='queued' WHERE id=?", (task["id"],))
                    continue
                follow = (
                    [{"kind": kinds[index + 1], "purpose": "Repair/retry or next role"}]
                    if index + 1 < len(kinds)
                    else []
                )
                self.store.finish(task, {"summary": "Finished", "next": follow})
            if mode == "auto":
                self.assertEqual(set(calls), {"codex", "claude"})
            else:
                self.assertEqual(set(calls), {mode.removesuffix("-only")})
            self.assertEqual(len(calls), len(kinds))

    def test_unknown_combination_and_uncertain_launch_never_spawn(self):
        self.store.start("routing", worker_roles={"assess": choice("codex", "unsupported")})
        task = self.store.claim("invalid")
        self.assertIn("Unsupported", task["worker_selection"]["error"])
        with patch("todo_flow.worker.select_launcher") as launcher:
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                run_worker(self.config, {}, task, self.store.path, None)
            launcher.assert_not_called()
        intent = self.store.path / ("native-task-" + task["id"] + ".json")
        intent.write_text("uncertain")
        self.store.start("routing", worker_mode="claude-only", worker_roles={})
        with patch("todo_flow.worker.select_launcher") as launcher:
            with self.assertRaises(FileExistsError):
                run_worker(self.config, {}, task, self.store.path, None)
            launcher.assert_not_called()
        self.assertEqual(intent.read_text(), "uncertain")

    def test_legacy_custom_adapter_and_exclusive_wait(self):
        legacy = Store(self.root / "legacy")
        config = {"worker": {"type": "command", "argv": ["adapter"]}, "worker_protocol": 2}
        legacy.configure(config)
        legacy.register(DOC)
        legacy.start("routing")
        self.assertIsNone(legacy.claim("legacy")["worker_selection"])
        self.assertEqual(apply(config, None), config)
        routing = {**request(None, config, DOC, mode="auto"), "requestId": "custom"}
        self.assertEqual(resolve(routing, "review")["adapter"], config["worker"])
        routing["mode"] = "claude-only"
        with self.assertRaisesRegex(ValueError, "No allowed worker"):
            resolve(routing, "review")
