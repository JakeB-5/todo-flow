"""Registered capacity/recovery conditions, using real independent drivers.

Barrier commands record starts/ends under a fixture lock. Capacity=1 must have
maximum overlap 1, cancellation must record zero starts, and driver loss must
not release capacity before the existing supervisor's attributed confirmation.
"""

import concurrent.futures
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_engine_verification_identity as identity_tests
import test_flow
import test_verification_identity_configuration as configuration_tests
from todo_flow import cli, verification
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory, launch_identity
from todo_flow.process_launch import LaunchGate
from todo_flow.process_recovery import require_recovery_clear
from todo_flow.release import check_config
from todo_flow.store import Conflict, Store
from todo_flow.verification_capacity import VerificationCapacity
from todo_flow.verification_evidence import require_current


RUNNER = """import fcntl,json,sys,time
from pathlib import Path
root=Path(sys.argv[1]); name=sys.argv[2]
def event(kind):
    with (root/'events.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps([kind,name])+'\\n'); stream.flush()
event('start')
(root/(name+'.started')).touch()
deadline=time.monotonic()+30
while not (root/(name+'.release')).exists():
    if time.monotonic()>deadline: raise RuntimeError('fixture release timeout')
    time.sleep(.02)
event('end')
"""

# Hold only the receipt, after physical cleanup. This distinguishes process exit
# from the durable evidence required by verification-wait-recovery.
SUPERVISOR = """import json,sys,time
from pathlib import Path
from todo_flow import process_supervisor
root=Path(sys.argv[2])
advance=process_supervisor.LaunchGate._advance
def delayed(self,event,state,reason,evidence):
    if state=='confirmed':
        (root/'cleanup-ready').touch()
        while not (root/'allow-confirm').exists(): time.sleep(.02)
    return advance(self,event,state,reason,evidence)
process_supervisor.LaunchGate._advance=delayed
raise SystemExit(process_supervisor.supervise(**json.loads(sys.argv[1])))
"""


def drive(specification):
    root = Path(specification["root"])
    original = subprocess.Popen

    def spawn(argv, **kwargs):
        if len(argv) > 1 and str(argv[1]).endswith("process_supervisor.py"):
            argv = [sys.executable, "-c", SUPERVISOR, argv[-1], str(root)]
        return original(argv, **kwargs)

    def verify(item):
        task, workspace = item
        engine = Engine(Store(specification["state"]))
        engine.config.update(
            verify=[sys.executable, str(root / "runner.py"), str(root), task["track"]],
            verify_identity={"version": 1, "files": [str(root / "runner.py")]},
            verify_timeout=30,
        )
        if specification["limit"] is not None:
            engine.config["verify_concurrency"] = specification["limit"]
        check_config(engine.config)
        try:
            with engine.process_attempt(task):
                result = engine.verify(task, workspace)
        except Exception as error:
            result = {"exception": type(error).__name__, "message": str(error)}
        result_path = root / (task["track"] + ".result")
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result))
        temporary.replace(result_path)

    interception = (
        patch("todo_flow.supervised_process.subprocess.Popen", side_effect=spawn)
        if specification.get("delay_confirmation")
        else contextlib.nullcontext()
    )
    with interception, concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(verify, specification["items"]))


class CapacityConfigurationTests(unittest.TestCase):
    setUp = configuration_tests.VerificationIdentityConfigurationTests.setUp
    args = configuration_tests.VerificationIdentityConfigurationTests.args

    def test_optional_cli_limit_and_validation_before_store_mutation(self):
        for value in (None, 1, 3):
            args = self.args()
            self.assertIsNone(args.verify_concurrency)
            args.verify_concurrency = value
            with patch("todo_flow.cli.command"), patch("todo_flow.cli.Store") as store:
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.initialize(args)
            config = store.return_value.configure.call_args.args[0]
            if value is None:
                self.assertNotIn("verify_concurrency", config)
            else:
                self.assertEqual(config["verify_concurrency"], value)
        parsed = cli.parser().parse_args(
            ["init", "--repo", str(self.root), "--verify", '["true"]', "--verify-concurrency", "2"]
        )
        self.assertEqual(parsed.verify_concurrency, 2)
        for invalid in (None, False, True, 0, -1, 1.5, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "verify_concurrency"):
                    check_config({"verify_concurrency": invalid})
        for invalid in (0, -1):
            args = self.args()
            args.verify_concurrency = invalid
            with patch("todo_flow.cli.command"), patch("todo_flow.cli.Store") as store:
                with self.assertRaisesRegex(ValueError, "verify_concurrency"):
                    cli.initialize(args)
            store.assert_not_called()
        store = Store(self.state)
        with patch.object(store, "transaction") as transaction:
            with self.assertRaisesRegex(ValueError, "verify_concurrency"):
                store.configure({"verify_concurrency": True})
        transaction.assert_not_called()


class CapacityDriverTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self, names):
        (self.root / "runner.py").write_text(RUNNER)
        self.items = []
        for name in names:
            if name != "addition":
                self.s.register({**test_flow.DOC, "id": name})
            self.s.start(name)
            task = self.s.claim("fixture-" + name)
            workspace = Engine(self.s).ensure_workspace(task)
            self.items.append((task, str(workspace)))
        self.addCleanup(self.release_all)
        return self.items

    def release_all(self):
        (self.root / "allow-confirm").touch()
        for task, _ in self.items:
            (self.root / (task["track"] + ".release")).touch()

    def await_value(self, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail("Capacity fixture did not reach its barrier")

    def dispose(self, process):
        self.release_all()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        # The surviving supervisor must finish before temporary state disappears.
        for task, _ in self.items:
            barrier = ProcessBarrier(self.s.path, task["track"])

            def stopped():
                try:
                    barrier.require_clear()
                    return True
                except ProcessBarrierError:
                    return False

            self.await_value(stopped)

    def driver(self, items, limit=1, *, delay_confirmation=False):
        specification = {
            "root": str(self.root),
            "state": str(self.s.path),
            "items": items,
            "limit": limit,
            "delay_confirmation": delay_confirmation,
        }
        source = Path(__file__).resolve().parents[1] / "src"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(source), *sys.path])}
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), json.dumps(specification)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.dispose, process)
        return process

    def rows(self, limit=1):
        return VerificationCapacity(self.s.path, limit).read()["requests"]

    def started(self):
        return {path.name.removesuffix(".started") for path in self.root.glob("*.started")}

    def result(self, name):
        path = self.root / (name + ".result")
        self.await_value(path.exists)
        return json.loads(path.read_text())

    def test_two_drivers_three_requests_share_one_slot_and_identifiable_fifo_waits(self):
        items = self.prepare(["addition", "second", "third"])
        first = self.driver(items[:2])
        second = self.driver(items[2:])
        self.await_value(lambda: len(self.rows()) == 3 and len(self.started()) == 1)
        rows = self.rows()
        self.assertEqual(sum(row["state"] == "admitted" for row in rows), 1)
        waiting = [row for row in rows if row["state"] == "waiting"]
        self.assertEqual(len(waiting), 2)
        for row in waiting:
            task = next(task for task, _ in items if task["track"] == row["track"])
            self.assertEqual((row["task"], row["attempt"]), (task["id"], task["attempt"]))
            self.assertTrue(row["execution"])
            self.assertIn("capacity", row["wait_reason"])
            self.assertEqual(row["stage"], "final")
        # Release one actual command at a time. Later stages/requests cannot jump
        # ahead of already registered waiters.
        for row in rows:
            self.await_value(lambda: row["track"] in self.started())
            (self.root / (row["track"] + ".release")).touch()
            self.assertTrue(self.result(row["track"])["ok"])
        self.assertEqual(first.wait(timeout=10), 0)
        self.assertEqual(second.wait(timeout=10), 0)
        active = maximum = 0
        starts = []
        for line in (self.root / "events.jsonl").read_text().splitlines():
            kind, name = json.loads(line)
            active += 1 if kind == "start" else -1
            maximum = max(maximum, active)
            if kind == "start":
                starts.append(name)
        self.assertEqual((maximum, active), (1, 0))
        self.assertEqual(starts, [row["track"] for row in rows])
        self.assertTrue(all(row["state"] == "released" for row in self.rows()))

    def test_omitted_limit_keeps_concurrent_execution_without_a_queue(self):
        items = self.prepare(["addition", "second", "third"])
        drivers = [self.driver(items[:2], None), self.driver(items[2:], None)]
        self.await_value(lambda: len(self.started()) == 3)
        self.assertFalse((self.s.path / "verification-capacity.json").exists())
        self.release_all()
        for driver in drivers:
            self.assertEqual(driver.wait(timeout=10), 0)
        for task, _ in items:
            self.assertTrue(self.result(task["track"])["ok"])

    def waiting_pair(self):
        items = self.prepare(["addition", "second"])
        holder = self.driver(items[:1])
        self.await_value(lambda: "addition" in self.started())
        waiter = self.driver(items[1:])
        self.await_value(
            lambda: any(
                row["track"] == "second" and row["state"] == "waiting" for row in self.rows()
            )
        )
        return holder, waiter

    def test_cancelled_waiter_never_executes_and_keeps_wait_reason(self):
        holder, waiter = self.waiting_pair()
        self.s.control("second", "cancel")
        self.assertEqual(self.result("second")["exception"], "Conflict")
        self.assertEqual(waiter.wait(timeout=10), 0)
        self.assertNotIn("second", self.started())
        row = next(row for row in self.rows() if row["track"] == "second")
        self.assertEqual(row["state"], "released")
        self.assertIn("capacity", row["wait_reason"])
        self.assertEqual(row["release_evidence"]["outcome"], "not-spawned")
        self.release_all()
        self.assertEqual(holder.wait(timeout=10), 0)
        self.assertNotIn("second", self.started())

    def test_head_changed_while_waiting_is_rejected_before_launch(self):
        holder, waiter = self.waiting_pair()
        workspace = self.items[1][1]
        command(["git", "commit", "--allow-empty", "-m", "Changed while waiting"], workspace)
        self.release_all()
        self.assertEqual(self.result("second")["exception"], "Conflict")
        self.assertNotIn("second", self.started())
        for driver in (holder, waiter):
            self.assertEqual(driver.wait(timeout=10), 0)

    def test_declared_input_changed_while_waiting_is_rejected_before_launch(self):
        holder, waiter = self.waiting_pair()
        (self.root / "runner.py").write_text(RUNNER + "\n# changed while waiting\n")
        self.release_all()
        result = self.result("second")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "VerificationIdentityError")
        self.assertNotIn("second", self.started())
        for driver in (holder, waiter):
            self.assertEqual(driver.wait(timeout=10), 0)

    def test_driver_death_keeps_slot_until_attributed_cleanup_confirmation(self):
        items = self.prepare(["addition", "second"])
        holder = self.driver(items[:1], delay_confirmation=True)
        self.await_value(lambda: "addition" in self.started())
        waiter = self.driver(items[1:])
        self.await_value(lambda: len(self.rows()) == 2)
        holder.kill()
        holder.wait(timeout=5)
        self.await_value((self.root / "cleanup-ready").exists)
        queue = VerificationCapacity(self.s.path, 1)
        with queue.locked():
            value = queue.read()
            queue.refresh(value)
        self.assertEqual(value["requests"][0]["state"], "admitted")
        self.assertEqual(value["requests"][1]["state"], "waiting")
        self.assertNotIn("second", self.started())
        first = value["requests"][0]
        gate = LaunchGate(str(self.s.path), first["track"], first["attempt"], first["execution"])
        self.assertNotEqual(gate._event()["state"], "confirmed")
        (self.root / "allow-confirm").touch()
        self.await_value(lambda: "second" in self.started())
        self.assertEqual(gate._event()["state"], "confirmed")
        self.assertEqual(gate._event()["evidence"]["completion"], "driver-disconnected")
        self.release_all()
        self.assertTrue(self.result("second")["ok"])
        self.assertEqual(waiter.wait(timeout=10), 0)


class CapacityEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue = VerificationCapacity(self.root, 1)

    def request(self, track="track"):
        task = {
            "track": track,
            "attempt": track + "-attempt",
            "id": track + "-task",
            "generation": 1,
        }
        execution = launch_identity(self.root, task)
        return task, execution

    def slot(self, task, execution):
        return self.queue.slot(
            task,
            execution,
            stage="final",
            workspace=self.root,
            head="fixture",
            scope="full",
            check=lambda: None,
        )

    def test_failure_before_prepare_releases_but_consumed_uncertain_launch_does_not(self):
        task, execution = self.request()
        with self.assertRaisesRegex(RuntimeError, "before dispatch"):
            with self.slot(task, execution):
                raise RuntimeError("before dispatch")
        self.assertEqual(self.queue.read()["requests"][0]["state"], "released")
        task, execution = self.request("uncertain")
        with self.slot(task, execution):
            gate = LaunchGate.prepare(**execution, backend="fixture")
            # Synthetic consumed gate: deliberately no owned exit proof.
            with gate.launching():
                pass
        ProcessInventory(self.root, task["track"], task["attempt"]).seal()
        value = self.queue.read()
        self.queue.refresh(value)
        self.assertEqual(value["requests"][-1]["state"], "admitted")
        self.assertIn("cleanup", value["requests"][-1]["reason"])

    def test_fenced_inventory_recovers_interruption_before_launch_intent(self):
        task, execution = self.request()
        context = self.slot(task, execution)
        context.__enter__()
        try:
            value = self.queue.read()
            self.queue.refresh(value)
            self.assertEqual(value["requests"][0]["state"], "admitted")
            # Models the existing recovery caller's execution-lock/claim fence.
            require_recovery_clear(
                self.root, task["track"], task["attempt"], task=task["id"], generation=1
            )
            other, next_execution = self.request("other")
            with self.slot(other, next_execution):
                self.assertEqual(self.queue.read()["requests"][0]["state"], "released")
        finally:
            context.__exit__(None, None, None)

    def test_spawn_failure_and_failed_command_release_only_with_confirmation(self):
        for track, failure in (("spawn", True), ("command", False)):
            task, execution = self.request(track)
            interception = (
                patch("todo_flow.supervised_process.subprocess.Popen", side_effect=OSError("spawn"))
                if failure
                else contextlib.nullcontext()
            )
            with self.assertRaises((OSError, RuntimeError)), interception:
                with self.slot(task, execution):
                    verification.run(
                        [sys.executable, "-c", "raise SystemExit(7)"],
                        self.root,
                        5,
                        launch_identity=execution,
                    )
            row = self.queue.read()["requests"][-1]
            self.assertEqual(row["state"], "released")
            self.assertEqual(LaunchGate(**execution)._event()["state"], "confirmed")
            self.assertIn("journal", row["release_evidence"])

    def test_corrupt_cleanup_evidence_holds_capacity(self):
        task, execution = self.request()
        with self.slot(task, execution):
            ProcessBarrier(self.root, task["track"]).path.write_bytes(b"{interrupted")
        row = self.queue.read()["requests"][0]
        self.assertEqual(row["state"], "admitted")
        self.assertIn("Cannot inspect", row["reason"])


class CapacityPolicyTests(unittest.TestCase):
    setUp = identity_tests.EngineVerificationIdentityTests.setUp
    tearDown = identity_tests.EngineVerificationIdentityTests.tearDown
    count = identity_tests.EngineVerificationIdentityTests.count

    def verifier(self, suffix=""):
        engine, task, workspace = identity_tests.EngineVerificationIdentityTests.verifier(
            self, suffix
        )
        engine.config["verify_concurrency"] = 1
        return engine, task, workspace

    def rows(self):
        return VerificationCapacity(self.s.path, 1).read()["requests"]

    def test_cache_new_head_and_dirty_checkout_preserve_existing_contract(self):
        identity_tests.EngineVerificationIdentityTests.test_stable_cache_reuses_execution_but_new_head_and_dirty_checkout_do_not(
            self
        )
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(all(row["state"] == "released" for row in self.rows()))

    def test_preflight_related_and_final_share_capacity_without_upgrading_partial(self):
        engine, task, workspace = self.verifier()
        engine.config["verify_preflight"] = [[sys.executable, "-c", "pass"]]
        engine.config["verify_related"] = [[sys.executable, "-c", "pass"]]
        partial = engine.verify(task, workspace, scope="partial")
        self.assertTrue(partial["ok"])
        self.assertFalse(self.counter.exists())
        self.assertEqual([row["stage"] for row in self.rows()], ["preflight-1", "related-1"])
        self.assertEqual(engine.verify(task, workspace, scope="partial"), partial)
        self.assertEqual(len(self.rows()), 2)
        with self.assertRaises(Conflict):
            require_current(engine.config, workspace, partial["head"], partial)
        with self.assertRaises(Conflict):
            engine.publish(task, workspace, test_flow.DOC)
        full = engine.verify(task, workspace)
        self.assertTrue(full["ok"])
        require_current(engine.config, workspace, full["head"], full)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            [row["stage"] for row in self.rows()],
            ["preflight-1", "related-1", "preflight-1", "final"],
        )

    def test_combined_integration_verification_uses_the_same_capacity(self):
        engine, task, workspace = self.verifier()
        command(["git", "commit", "--allow-empty", "-m", "Candidate"], workspace)
        head = command(["git", "rev-parse", "HEAD"], workspace)
        engine.update(task, head=head)
        self.assertTrue(engine.verify(task, workspace)["ok"])
        self.s.finish(
            task, {"summary": "Candidate", "next": [{"kind": "review", "purpose": "Review"}]}
        )
        reviewer = self.s.claim("reviewer")
        engine.record_review(
            reviewer,
            {
                "summary": "Independent review fixture",
                "verdict": "met",
                "conditions": [{"id": "sum", "verdict": "met", "evidence": "Fixture"}],
            },
        )
        self.s.finish(
            reviewer, {"summary": "Reviewed", "next": [{"kind": "land", "purpose": "Land"}]}
        )
        landing = self.s.claim("lander")
        engine.land(landing)
        receipt = json.loads(self.s.track("addition")["landing"])
        self.assertEqual(self.count(), 2)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["head"], receipt["merged"])
        self.assertEqual(rows[-1]["scope"], "full")
        self.assertEqual(Path(rows[-1]["workspace"]).parent, self.s.path / "integrations")
        self.assertTrue(all(row["state"] == "released" for row in rows))


if __name__ == "__main__":
    drive(json.loads(sys.argv[1]))
