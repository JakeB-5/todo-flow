"""Real local worker processes; native-only fields use explicit synthetic receipts."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.cli import dispatch, parser
from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.supervised_process import SupervisedProcess
from todo_flow.worker import run_worker
from todo_flow.worker_stop import worker_stop_summary


class WorkerStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_case(self, backend, code, *, timeout=None, adapter="command"):
        state = self.root / "state"
        task = {
            "track": "example",
            "attempt": "attempt-one",
            "id": "task-one",
            "generation": 1,
            "kind": "work",
        }
        argv = [sys.executable, "-c", code]
        config = {
            "worker_protocol": 2,
            "worker_launcher": backend,
            "worker_timeout": timeout,
            "worker": {"type": adapter, "argv": argv},
        }
        if backend == "terminal":
            launcher = (
                "import shlex,subprocess,sys;"
                "subprocess.Popen(shlex.split(sys.argv[1]),"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)"
            )
            config["terminal_command"] = [sys.executable, "-c", launcher, "{command}"]
        result = None
        error = None

        def spawn(args, **kwargs):
            return SupervisedProcess(argv, **kwargs)

        with patch("todo_flow.worker.SupervisedProcess", spawn):
            try:
                result = run_worker(
                    config, {"workspace": str(self.root)}, task, state, lambda _: None
                )
            except (RuntimeError, ValueError, TimeoutError) as caught:
                error = caught
        summary = worker_stop_summary(state, task["track"], task["attempt"])
        self.assertEqual(summary["support"], "current")
        self.assertEqual(len(summary["executions"]), 1)
        row = summary["executions"][0]
        self.assertTrue(row["process"]["started"])
        self.assertTrue(row["process"]["group_exit_confirmed"])
        self.assertIsNone(row["usage"])
        self.assertIsNone(row["cost"])
        self.assertIsNone(row["goal_success"])
        if backend == "terminal":
            self.assertEqual(row["receipts"]["terminal"]["record"]["status"], "exited")
        return result, error, summary, row

    def test_headless_and_terminal_valid_proposals_are_not_goal_success(self):
        for backend in ("headless", "terminal"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp).resolve()
                result, error, summary, row = self.run_case(
                    backend, """print('{"summary": "ok"}')"""
                )
                self.assertIsNone(error)
                self.assertEqual(result, {"summary": "ok"})
                self.assertEqual(row["process"]["returncode"], 0)
                self.assertEqual(row["proposal"]["status"], "valid")
                self.assertTrue(row["proposal"]["parsed"])
                self.assertTrue(row["proposal"]["validated"])
                self.assertIsNone(row["proposal"]["reason"])
                self.assertTrue(Path(summary["artifacts"]["output.json"]).is_file())

    def test_failure_truncation_and_validation_remain_distinct(self):
        cases = (
            ("import sys;print('Weekly limit reached');sys.exit(7)", "execution", None, None),
            ("print('{')", "decoding", False, None),
            ("print('{}')", "validation", True, False),
        )
        for backend in ("headless", "terminal"):
            for code, phase, parsed, validated in cases:
                with (
                    self.subTest(backend=backend, phase=phase),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    self.root = Path(tmp).resolve()
                    result, error, summary, row = self.run_case(backend, code)
                    self.assertIsNone(result)
                    self.assertIsNotNone(error)
                    proposal = row["proposal"]
                    self.assertEqual(proposal["status"], "error")
                    self.assertEqual(proposal["phase"], phase)
                    self.assertIs(proposal["parsed"], parsed)
                    self.assertIs(proposal["validated"], validated)
                    self.assertEqual(proposal["error"]["message"], str(error))
                    if phase == "execution":
                        self.assertEqual(row["process"]["returncode"], 7)
                        self.assertIn("Weekly limit reached", proposal["error"]["message"])
                        self.assertIsNone(proposal["reason"])
                    else:
                        self.assertEqual(row["process"]["returncode"], 0)
                        self.assertEqual(proposal["reason"], "protocol-error")
                    self.assertTrue(Path(summary["artifacts"]["output.json"]).is_file())

    def test_timeout_is_observed_without_usage_inference(self):
        for backend in ("headless", "terminal"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp).resolve()
                _, error, _, row = self.run_case(backend, "import time;time.sleep(60)", timeout=2)
                self.assertIsInstance(error, TimeoutError)
                self.assertEqual(row["proposal"]["reason"], "timeout")
                self.assertIsNone(row["proposal"]["parsed"])

    def test_provider_error_keeps_original_without_classifying_quota(self):
        message = "Synthetic provider limit: unknown-provider-code"
        code = "import json;print(json.dumps(" + repr({"is_error": True, "result": message}) + "))"
        _, error, _, row = self.run_case("headless", code, adapter="claude")
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(row["proposal"]["phase"], "provider")
        self.assertEqual(row["proposal"]["reason"], "provider-error")
        self.assertIn(message, row["proposal"]["error"]["message"])
        self.assertIsNone(row["proposal"]["parsed"])

    def inventory(self, attempt="attempt-one", generation=1):
        inventory = ProcessInventory(self.root, "example", attempt, "task-one", generation)
        inventory.start()
        identity = inventory.register()
        return inventory, identity

    def test_legacy_unknown_and_unsupported_formats_do_not_become_success(self):
        inventory, identity = self.inventory()
        gate = LaunchGate.prepare(**identity, backend="synthetic")
        event = gate._event()
        gate._advance(event, "unknown", "Synthetic unknown reason", {"completion": "future-reason"})
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        summary = worker_stop_summary(self.root, "example", "attempt-one")
        row = summary["executions"][0]
        self.assertEqual(summary["support"], "legacy")
        self.assertEqual(row["process"]["reason"], "future-reason")
        for key in ("started", "group_exit_confirmed", "returncode"):
            self.assertIsNone(row["process"][key])
        self.assertIsNone(row["proposal"])
        self.assertIsNone(row["usage"])
        self.assertIsNone(row["cost"])
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        )
        value = inventory.read()
        value["worker_results"] = {"version": 99, "executions": {}}
        inventory.write(value)
        with self.assertRaises(ProcessBarrierError):
            worker_stop_summary(self.root, "example", "attempt-one")

    def test_duplicate_late_and_reassigned_observations_are_fenced(self):
        inventory, identity = self.inventory()
        inventory.prepared(identity["execution"])
        observation = {
            "status": "valid",
            "phase": "validation",
            "parsed": True,
            "validated": True,
        }
        inventory.observe_worker(identity["execution"], observation)
        original = inventory.path.read_bytes()
        inventory.observe_worker(identity["execution"], observation)
        self.assertEqual(inventory.path.read_bytes(), original)
        with self.assertRaises(ProcessBarrierError):
            inventory.observe_worker(identity["execution"], {**observation, "status": "error"})
        with self.assertRaises(ProcessBarrierError):
            ProcessInventory(self.root, "example", "attempt-one", "task-one", 2).observe_worker(
                identity["execution"], observation
            )
        inventory.seal()
        sealed = inventory.path.read_bytes()
        with self.assertRaises(ProcessBarrierError):
            inventory.observe_worker(identity["execution"], observation)
        newer, new_identity = self.inventory("attempt-two", generation=2)
        before_new = newer.path.read_bytes()
        with self.assertRaises(ProcessBarrierError):
            newer.observe_worker(identity["execution"], observation)
        self.assertEqual(newer.path.read_bytes(), before_new)
        self.assertEqual(inventory.path.read_bytes(), sealed)
        row = worker_stop_summary(self.root, "example", "attempt-two")["executions"][0]
        self.assertEqual(row["execution"], new_identity["execution"])
        self.assertIsNone(row["proposal"])

    def test_cancelled_launch_and_restart_query_preserve_nonlaunch_proof(self):
        _, identity = self.inventory()
        gate = LaunchGate.prepare(**identity, backend="synthetic")
        gate.cancel_pending()
        row = worker_stop_summary(self.root, "example", "attempt-one")["executions"][0]
        self.assertFalse(row["process"]["started"])
        self.assertFalse(row["process"]["group_exit_confirmed"])
        self.assertIsNone(row["process"]["returncode"])
        self.assertEqual(
            row["process"]["events"],
            ProcessBarrier(self.root, "example").history(),
        )
        with self.assertRaises(ProcessBarrierError):
            with LaunchGate(**identity).launching():
                self.fail("Cancelled launch must not restart")

    def test_native_fixture_links_original_failure_and_rejects_wrong_task_or_version(self):
        _, identity = self.inventory()
        task = {"track": "example", "attempt": "attempt-one", "id": "task-one"}
        folder = self.root / "attempts" / "attempt-one"
        folder.mkdir(parents=True)
        spec = {"process_identity": identity, "execution": identity["execution"], "task": task}
        (folder / "native-spec.json").write_text(json.dumps(spec))
        record = {
            "version": 1,
            "task": task,
            "status": "failed",
            "server_exit": 9,
            "failure": {"type": "ProviderError", "message": "Synthetic unknown limit"},
        }
        path = folder / "native-session.json"
        path.write_text(json.dumps(record))
        row = worker_stop_summary(self.root, "example", "attempt-one")["executions"][0]
        self.assertEqual(row["receipts"]["native"]["record"], record)
        self.assertIsNone(row["process"]["started"])
        self.assertIsNone(row["process"]["group_exit_confirmed"])
        self.assertIsNone(row["proposal"])
        self.assertIsNone(row["usage"])
        path.write_text(json.dumps({**record, "task": {**task, "id": "foreign"}}))
        with self.assertRaisesRegex(ValueError, "Misattributed"):
            worker_stop_summary(self.root, "example", "attempt-one")
        path.write_text(json.dumps({**record, "version": 2}))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            worker_stop_summary(self.root, "example", "attempt-one")

    def test_cli_reads_legacy_receipt_without_creating_state_records(self):
        folder = self.root / "attempts" / "old"
        folder.mkdir(parents=True)
        receipt = folder / "terminal-process.json"
        receipt.write_text(json.dumps({"status": "exited", "returncode": 0}))
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = io.StringIO()
        with redirect_stdout(output):
            dispatch(
                parser().parse_args(["--state", str(self.root), "worker-stop", "example", "old"])
            )
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["support"], "legacy")
        self.assertEqual(summary["executions"], [])
        self.assertEqual(summary["unattributed_receipts"][0]["path"], str(receipt))
        self.assertEqual(
            before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        )
