"""Regression boundaries for test-only runtime registration and maintenance state."""

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_file_store
import test_flow
import test_language
import test_updates
import test_web
import test_worker_routing
from runtime_home import isolate_runtime_home
from todo_flow import engine_updates, skill_updates
from todo_flow.engine import Engine
from todo_flow.maintenance import home, known_states, project_lock, runtime_guard
from todo_flow.release import CONTRACTS, VERSION, release_number
from todo_flow.store import Store


DRIVER = """
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import test_flow
from todo_flow.engine import Engine
from todo_flow.maintenance import home, write_json

# The parent owns these directories even when SIGKILL prevents every cleanup.
tempfile.tempdir = sys.argv[1]
fixture = test_flow.IntegrationTests()
fixture.setUp()
fixture.s.start("addition")


def wait_at_worker(config, context, task, state, heartbeat):
    write_json(Path(sys.argv[2]), {
        "home": str(home()),
        "state": str(fixture.s.path),
        "task": task["id"],
    })
    sys.stdin.read()
    raise AssertionError("The parent must kill the driver at the ready boundary")


with patch("todo_flow.engine.run_worker", side_effect=wait_at_worker):
    Engine(fixture.s).run(jobs=1, max_tasks=1)
"""


def stop_process(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=10)


class RuntimeHomeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # Never use the developer's actual home, even when testing the old bug.
        self.real_home = self.root / "caller-home"
        self.enterContext(patch.dict(os.environ, {"TODO_FLOW_HOME": str(self.real_home)}))
        self.real_store = Store(self.root / "real-state")
        self.real_store.configure({"github": None, "endpoint": "review"})
        self.real_store.register(test_flow.DOC)
        with runtime_guard(self.real_store.path):
            pass
        (self.real_home / "sentinel.bin").write_bytes(b"caller data\x00\xff\n")

    def snapshot(self):
        return {
            str(path.relative_to(self.real_home)): None if path.is_dir() else path.read_bytes()
            for path in self.real_home.rglob("*")
        }

    def plan_update(self):
        # Only installation/wheel discovery is synthetic. Exercise the real
        # engine dry-run lock and known-project preflight without installing.
        major, _, _ = release_number(VERSION)
        candidate = {"version": f"{major + 1}.0.0", "contracts": CONTRACTS}
        with (
            patch.object(engine_updates, "installation", return_value={}),
            patch.object(engine_updates, "inspect_wheel", return_value=candidate),
        ):
            return engine_updates.launch("synthetic.whl", dry_run=True)

    def assert_only_real_project(self):
        self.assertEqual(
            [row["state"] for row in self.plan_update()["projects"]],
            [str(self.real_store.path)],
        )

    def test_parent_parallel_cli_children_and_update_records_stay_in_fixture_home(self):
        # test-home-contained: project paths alone used to be temporary while
        # Engine.run and child CLI registration still modified caller-home.
        before = self.snapshot()
        fixture = test_flow.IntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        isolated = home()
        try:
            self.assertNotEqual(isolated, self.real_home)
            self.assertEqual(Engine(fixture.s).run(max_tasks=1), 0)
            child_states = [fixture.root / f"child-{index}" for index in range(2)]
            for state in child_states:
                Store(state).configure({"github": None, "endpoint": "review"})

            def status(state):
                return subprocess.run(
                    [sys.executable, "-m", "todo_flow", "--state", str(state), "status"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

            # Set the environment once before concurrency; children inherit it.
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(status, child_states))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            expected = {fixture.s.path.resolve(), *(state.resolve() for state in child_states)}
            self.assertEqual(set(known_states()), expected)
            self.assertTrue((isolated / "runtime.lock").is_file())
            for state in expected:
                self.assertTrue(project_lock(state).is_file())

            source = fixture.root / "bundle"
            (source / "todo").mkdir(parents=True)
            (source / "todo/SKILL.md").write_text("Synthetic skill instructions\n")
            receipt = skill_updates.update(
                fixture.root / "skills", fixture.s.path, install=True, source=source
            )
            self.assertTrue(
                (isolated / "skill-updates" / receipt["backup"] / "receipt.json").is_file()
            )
            self.assertEqual(self.snapshot(), before)
        finally:
            # Direct fixture callers must restore the environment too.
            fixture.tearDown()
        self.assertEqual(os.environ["TODO_FLOW_HOME"], str(self.real_home))
        self.assertFalse(isolated.exists())
        self.assert_only_real_project()
        self.assertEqual(self.snapshot(), before)

    def test_independent_cli_fixtures_leave_caller_files_and_bytes_unchanged(self):
        cases = (
            test_file_store.FileStoreTests(
                "test_concurrent_cli_requests_coalesce_and_no_worker_on_request_only"
            ),
            test_web.HttpTests("test_pause_resume_and_static_assets"),
            test_language.LanguageTests("test_installed_skills_inherit_project_language_and_state"),
            test_worker_routing.WorkerRoutingTests(
                "test_request_only_restart_resume_and_explicit_reselection"
            ),
        )
        before = self.snapshot()
        for case in cases:
            with self.subTest(fixture=type(case).__name__):
                result = unittest.TestResult()
                case.run(result)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertEqual(os.environ["TODO_FLOW_HOME"], str(self.real_home))
                self.assertEqual(self.snapshot(), before)

    def test_setup_failures_restore_the_callers_home(self):
        # real-update-protection: unittest skips tearDown after a setUp error.
        fixtures = (
            (test_flow.IntegrationTests, "test_flow.command"),
            (test_file_store.FileStoreTests, "test_file_store.Store"),
            (test_web.HttpTests, "test_web.Store"),
            (test_language.LanguageTests, "test_language.command"),
            (test_worker_routing.WorkerRoutingTests, "test_worker_routing.Store"),
            (test_updates.UpdateTests, "test_updates.Store"),
        )
        before = self.snapshot()
        for fixture_type, target in fixtures:
            with self.subTest(fixture=fixture_type.__name__):
                visited = []

                def fail_setup(*args, **kwargs):
                    visited.append(Path(os.environ["TODO_FLOW_HOME"]))
                    raise RuntimeError("synthetic setup failure")

                method = unittest.defaultTestLoader.getTestCaseNames(fixture_type)[0]
                case = fixture_type(method)
                result = unittest.TestResult()
                with patch(target, side_effect=fail_setup):
                    case.run(result)
                self.assertEqual(len(result.errors), 1, result.errors)
                self.assertIn("synthetic setup failure", result.errors[0][1])
                self.assertEqual(len(visited), 1)
                self.assertNotEqual(visited[0], self.real_home)
                self.assertFalse(visited[0].exists())
                self.assertFalse(Path(case.tmp.name).exists())
                self.assertEqual(os.environ["TODO_FLOW_HOME"], str(self.real_home))
                self.assertEqual(self.snapshot(), before)

    def test_nested_and_explicit_homes_restore_in_reverse_order(self):
        outer = unittest.TestCase()
        self.addCleanup(outer.doCleanups)
        outer_home = isolate_runtime_home(outer)
        inner = unittest.TestCase()
        self.addCleanup(inner.doCleanups)
        inner_home = isolate_runtime_home(inner)
        self.assertNotEqual(inner_home, outer_home)
        inner.doCleanups()
        self.assertEqual(home(), outer_home)
        self.assertFalse(inner_home.exists())

        # Existing update fixtures deliberately select their own temporary home.
        explicit = test_updates.UpdateTests()
        self.addCleanup(explicit.doCleanups)
        explicit.setUp()
        try:
            self.assertEqual(home(), explicit.root / "control")
            explicit.install()
            self.assertTrue(list((home() / "skill-updates").glob("*/receipt.json")))
        finally:
            explicit.tearDown()
        self.assertEqual(home(), outer_home)
        outer.doCleanups()
        self.assertEqual(os.environ["TODO_FLOW_HOME"], str(self.real_home))
        self.assertFalse(outer_home.exists())

    def test_cleanup_restores_an_absent_home_override(self):
        with patch.dict(os.environ):
            os.environ.pop("TODO_FLOW_HOME", None)
            case = unittest.TestCase()
            isolated = isolate_runtime_home(case)
            try:
                self.assertEqual(home(), isolated)
            finally:
                case.doCleanups()
            self.assertNotIn("TODO_FLOW_HOME", os.environ)
            self.assertFalse(isolated.exists())

    def test_killed_parallel_drivers_do_not_enter_real_update_preflight(self):
        # interrupted-test-contained: wait for actual Engine.run registration
        # and a claimed running task, then kill without any child teardown.
        before = self.snapshot()
        repo = Path(__file__).resolve().parents[1]
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(repo / "tests"), str(repo / "src"), os.environ.get("PYTHONPATH", "")]
            ),
        }
        drivers = []
        for index in range(2):
            directory = self.root / f"driver-{index}"
            directory.mkdir()
            ready = directory / "ready.json"
            process = subprocess.Popen(
                [sys.executable, "-c", DRIVER, str(directory), str(ready)],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(stop_process, process)
            drivers.append((process, ready))

        deadline = time.monotonic() + 30
        while not all(ready.is_file() for _, ready in drivers):
            for process, _ in drivers:
                if process.poll() is not None:
                    _, error = process.communicate()
                    self.fail(f"Driver exited before ready: {error}")
            if time.monotonic() >= deadline:
                self.fail("Drivers did not reach the registered running boundary")
            time.sleep(0.02)

        records = [json.loads(ready.read_text()) for _, ready in drivers]
        for process, _ in drivers:
            self.assertIsNone(process.poll())
            process.kill()
            process.communicate(timeout=10)
            self.assertLess(process.returncode, 0)
        self.assertEqual(len({row["home"] for row in records}), 2)
        for row in records:
            isolated = Path(row["home"])
            state = Path(row["state"])
            self.assertNotEqual(isolated, self.real_home)
            self.assertTrue(isolated.is_relative_to(self.root))
            self.assertTrue((isolated / "runtime.lock").is_file())
            self.assertEqual(
                json.loads((state / "tasks" / (row["task"] + ".json")).read_text())["status"],
                "running",
            )
            with patch.dict(os.environ, {"TODO_FLOW_HOME": str(isolated)}):
                self.assertEqual(known_states(), [state.resolve()])
                self.assertTrue(project_lock(state).is_file())
                # The abandoned work still blocks its own home: it is isolated,
                # not deleted, reconciled or ignored by the updater.
                with self.assertRaisesRegex(RuntimeError, "running work"):
                    self.plan_update()
        self.assert_only_real_project()
        self.assertEqual(self.snapshot(), before)

    def test_real_runtime_and_unresolved_work_still_block_engine_updates(self):
        # real-update-protection: isolation must not weaken the real preflight.
        with runtime_guard(self.real_store.path):
            with self.assertRaisesRegex(RuntimeError, "active runtime"):
                self.plan_update()
        self.assert_only_real_project()
        self.real_store.start("addition")
        self.real_store.claim("abandoned-real-driver")
        with self.assertRaisesRegex(RuntimeError, "running work"):
            self.plan_update()


if __name__ == "__main__":
    unittest.main()
