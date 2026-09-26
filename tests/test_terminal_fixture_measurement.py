import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from todo_flow.worker import run_worker

SOURCE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "terminal_fixture_measurement", SOURCE / "scripts/measure_terminal_fixtures.py"
)
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


class TerminalFixtureMeasurementTests(unittest.TestCase):
    def fixture(self, backend):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = measurement.Fixture(SOURCE, Path(temporary.name), backend)
        self.addCleanup(fixture.close)
        return fixture

    def bridge_command(self, fixture, name="direct", exec_bridge=False):
        folder = fixture.state / "attempts" / name
        folder.mkdir(parents=True)
        task = {"id": name, "attempt": name}
        fixture.register(folder, task)
        measurement.save(
            folder / "fixture.json",
            {"hold": False, "release": str(fixture.release), "barrier_timeout": 1},
        )
        measurement.save(
            folder / "input.json",
            {"task": task, "paths": {"fixture": str(folder / "fixture.json")}},
        )
        measurement.save(
            folder / "terminal-spec.json",
            {"argv": fixture.argv, "cwd": str(SOURCE), "title": "TODO fixture"},
        )
        command = shlex.join(
            [
                sys.executable,
                str(SOURCE / "src/todo_flow/terminal_worker.py"),
                str(folder / "terminal-spec.json"),
            ]
        )
        return folder, ("exec " if exec_bridge else "") + command

    def test_unsupported_cli_is_recorded_and_rejected_without_dispatch(self):
        fixture = self.fixture("tmux")
        for argv in (
            ["tmux", "-S", fixture.socket_path, "kill-server"],
            ["tmux", "-S", fixture.socket_path, "new-window", "unexpected"],
            ["curl", "https://example.invalid"],
        ):
            with self.assertRaises(ValueError):
                fixture.cli(argv)
        events = [json.loads(line) for line in (fixture.output / "cli.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 3)
        self.assertTrue(all("error" in event for event in events))
        self.assertEqual(fixture.bridges, {})

    def test_bridge_and_worker_command_validation_precedes_process_creation(self):
        fixture = self.fixture("custom")
        folder, command = self.bridge_command(fixture)
        with self.assertRaises(ValueError):
            fixture.cli(["fixture-terminal", command + " extra"])
        spec = json.loads((folder / "terminal-spec.json").read_text())
        spec["argv"] = [sys.executable, "-c", "raise SystemExit(99)"]
        measurement.save(folder / "terminal-spec.json", spec)
        with self.assertRaises(ValueError):
            fixture.cli(["fixture-terminal", command])
        self.assertEqual(fixture.bridges, {})
        self.assertFalse((folder / "worker-start.json").exists())

    def test_baseline_nonexec_shell_survives_real_bridge_exit(self):
        fixture = self.fixture("orca")
        folder, command = self.bridge_command(fixture)
        result = fixture.cli(
            [
                "fixture-orca",
                "terminal",
                "create",
                "--worktree",
                "id:fixture",
                "--title",
                "TODO fixture",
                "--command",
                command,
                "--json",
            ]
        )
        handle = json.loads(result.stdout)["result"]["terminal"]["handle"]
        fixture.bridges[handle]["process"].wait(timeout=10)
        fixture.sample("after-real-bridge")
        self.assertTrue(fixture.rows[handle]["connected"])
        self.assertTrue(fixture.rows[handle]["writable"])
        self.assertTrue(
            (folder / "fixture-bridge.stdout").read_text().rstrip().endswith(
                "TODO Flow worker exited: 0"
            )
        )
        receipt = json.loads((folder / "fixture-bridge-exit.json").read_text())
        self.assertEqual(receipt["returncode"], 0)
        waited = fixture.cli(
            [
                "fixture-orca",
                "terminal",
                "wait",
                "--terminal",
                handle,
                "--for",
                "exit",
                "--timeout-ms",
                "1000",
                "--json",
            ]
        )
        self.assertFalse(json.loads(waited.stdout)["result"]["wait"]["satisfied"])
        with self.assertRaises(ValueError):
            fixture.cli(["fixture-orca", "terminal", "close", "--terminal", handle, "--json"])
        self.assertIn(handle, fixture.rows)

    def test_candidate_saturation_blocks_third_before_create_then_retries(self):
        for backend in ("orca", "tmux"):
            with self.subTest(backend=backend):
                fixture = self.fixture(backend)
                fixture.measure(run_worker, True, "saturation", count=4)
                report = fixture.report()
                self.assertTrue(report["measurement_complete"], report)
                self.assertEqual(report["completed"], 4)
                self.assertEqual(report["blocked_calls"], 1)
                self.assertEqual(report["blocked_requests"], 0)
                self.assertEqual(report["created"], 4)
                self.assertEqual(report["reclaimed"], 4)
                self.assertEqual(report["terminal_admission"], "blocked")
                self.assertEqual(report["max_active_worker_processes"], 2)
                self.assertEqual(report["max_owned_tabs"], 2)
                self.assertEqual(report["preserved"], 0)
                self.assertTrue((fixture.output / "barrier-pair.json").is_file())
                for bridge in fixture.bridges.values():
                    self.assertEqual(bridge["process"].returncode, 0)
                    log = bridge["log"].read_text()
                    self.assertIn("fixture worker request-", log)
                    self.assertTrue(log.rstrip().endswith("TODO Flow worker exited: 0"))
                fixture.check_evidence()

    def test_custom_preserves_shell_tabs_and_blocks_remaining_requests(self):
        fixture = self.fixture("custom")
        fixture.measure(run_worker, True, "saturation", count=5)
        report = fixture.report()
        self.assertTrue(report["measurement_complete"], report)
        self.assertEqual(report["completed"], 2)
        self.assertEqual(report["created"], 2)
        self.assertEqual(report["preserved"], 2)
        self.assertEqual(report["blocked_requests"], 3)
        self.assertEqual(report["close_calls"], 0)
        self.assertTrue(all(row["connected"] for row in fixture.rows.values()))
        self.assertTrue(all(b["process"].returncode == 0 for b in fixture.bridges.values()))
        self.assertEqual(fixture.samples[-1]["active_worker_pids"], [])
        self.assertEqual(fixture.samples[-1]["active_tabs"], 2)

    def test_headless_measures_real_overlap_without_terminal_admission(self):
        fixture = self.fixture("headless")
        fixture.measure(run_worker, True, "saturation", count=3)
        report = fixture.report()
        self.assertTrue(report["measurement_complete"], report)
        self.assertEqual(report["completed"], 3)
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["max_active_tabs"], 0)
        self.assertEqual(report["max_active_worker_processes"], 2)
        self.assertEqual(report["blocked_calls"], 0)
        self.assertEqual(report["terminal_admission"], "not-applicable")

    def test_barrier_wait_timeout_releases_and_collects_real_workers(self):
        fixture = self.fixture("headless")
        with self.assertRaises(TimeoutError):
            fixture.measure(run_worker, True, "saturation", count=3, barrier_wait=0)
        self.assertTrue(fixture.release.exists())
        self.assertTrue(all(future.done() for future in fixture.futures))
        self.assertFalse(fixture.monitor.is_alive())
        self.assertFalse(fixture.report()["measurement_complete"])
        self.assertEqual(fixture.sample("after-timeout")["active_worker_pids"], [])

    def test_worker_barrier_has_its_own_timeout(self):
        fixture = self.fixture("headless")
        folder = fixture.state / "timeout-worker"
        folder.mkdir()
        measurement.save(
            folder / "fixture.json",
            {"hold": True, "release": str(fixture.release), "barrier_timeout": 0.05},
        )
        context = {"task": {"id": "timeout"}, "paths": {"fixture": str(folder / "fixture.json")}}
        result = subprocess.run(
            fixture.argv,
            input=json.dumps(context),
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((folder / "worker-barrier-timeout.json").is_file())
        self.assertFalse((folder / "worker-finish.json").exists())

    def test_concurrent_inventory_and_cli_observe_consistent_snapshots(self):
        fixture = self.fixture("tmux")
        barrier = threading.Barrier(3, timeout=10)

        def launch():
            barrier.wait()
            for number in range(3):
                _, command = self.bridge_command(fixture, f"concurrent-{number}")
                fixture.cli(
                    [
                        "tmux",
                        "-S",
                        fixture.socket_path,
                        "new-window",
                        "-d",
                        "-P",
                        "-F",
                        "#{window_id}",
                        "-n",
                        "TODO fixture",
                        "-c",
                        str(SOURCE),
                        command,
                    ]
                )

        def observe():
            barrier.wait()
            for _ in range(30):
                fixture.cli(
                    ["tmux", "-S", fixture.socket_path, "list-windows", "-a", "-F", "#{window_id}"]
                )
                fixture.sample("concurrent-reader")

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(launch), pool.submit(observe), pool.submit(observe)]
            for future in futures:
                future.result(timeout=20)
        fixture.reap_bridges()
        final = fixture.sample("collected")
        self.assertEqual(final["owned_tabs"], 0)
        self.assertEqual(len(fixture.bridges), 3)
        samples = [json.loads(line) for line in (fixture.output / "samples.jsonl").read_text().splitlines()]
        self.assertEqual([row["sequence"] for row in samples], list(range(len(samples))))
        for row in samples:
            self.assertEqual(row["owned_tabs"], len(row["inventory"]))
            self.assertEqual(
                row["active_tabs"], sum(tab["connected"] for tab in row["inventory"])
            )
        for line in (fixture.output / "cli.jsonl").read_text().splitlines():
            self.assertEqual(json.loads(line)["returncode"], 0)
