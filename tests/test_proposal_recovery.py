import base64
import copy
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_change_proposals
import test_flow
import test_integration_repair
from todo_flow import integration, proposal_application as application
from todo_flow.adapters import command
from todo_flow.checkout import snapshot
from todo_flow.engine import Engine
from todo_flow.store import Conflict
from todo_flow.worker import result_payload, run_worker


CHILD = """
import json, os, signal, sys
from pathlib import Path
from unittest.mock import patch
from todo_flow import proposal_application as application
from todo_flow.adapters import file_lock
from todo_flow.engine import Engine
from todo_flow.store import Store

data = json.load(sys.stdin)
engine = Engine(Store(data["state"]))
workspace = Path(data["workspace"])
original_replace = application.os.replace
original_command = application.command

def die():
    os.kill(os.getpid(), signal.SIGKILL)

def replace(source, target):
    original_replace(source, target)
    if Path(target) == workspace / data["changes"][0]["path"] and data["boundary"] == "file":
        record = application.read(engine, data["task"]["track"])
        assert record["phase"] == "applying"
        assert record["intent"]["files"][0]["before"] is not None
        die()

def command(argv, *args, **kwargs):
    if argv[:2] == ["git", "commit"] and data["boundary"] == "before-commit":
        die()
    result = original_command(argv, *args, **kwargs)
    if argv[:2] == ["git", "commit"] and data["boundary"] == "after-commit":
        die()
    return result

with file_lock(engine.store.path / "locks" / (data["task"]["track"] + ".lock")):
    with patch.object(application.os, "replace", replace), patch.object(application, "command", command):
        engine.apply_changes(
            data["task"], workspace, data["changes"], data["repair"],
            expected_head=data["head"], result=data["result"]
        )
raise AssertionError("Expected a real process interruption")
"""


def interrupt(case, engine, task, workspace, changes, boundary, repair=None):
    payload = {
        "state": str(engine.store.path),
        "workspace": str(workspace),
        "task": task,
        "head": command(["git", "rev-parse", "HEAD"], workspace),
        "changes": changes,
        "boundary": boundary,
        "repair": repair,
        "result": {"summary": "Preserved proposal", "changes": changes, "verify": True},
    }
    process = subprocess.run(
        [sys.executable, "-c", CHILD],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=30,
    )
    case.assertEqual(process.returncode, -signal.SIGKILL, process.stderr + process.stdout)
    return application.read(engine, task["track"])


def restore_original(workspace, record):
    for row in record["intent"]["files"]:
        target = workspace / row["path"]
        before = row["before"]
        if before is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(base64.b64decode(before["bytes"]))
            target.chmod(before["mode"])
    index = Path(command(["git", "rev-parse", "--git-path", "index"], workspace))
    if not index.is_absolute():
        index = workspace / index
    index.write_bytes(base64.b64decode(record["intent"]["index"]))


class InterruptedProposalTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    workspace = test_change_proposals.ProposalApplicationTests.workspace

    def changes(self, workspace):
        return [
            test_change_proposals.replacement(workspace),
            {"path": "new.py", "content": "# 새 파일 🌿\n"},
        ]

    def assert_blocked(self, engine, task, workspace):
        with patch("todo_flow.engine.run_verification") as verify:
            for action in (
                lambda: engine.verify(task, workspace),
                lambda: engine.publish(task, workspace, {}),
                lambda: engine.record_review(task, {}),
                lambda: engine.gate(task),
                lambda: engine.land(task),
                lambda: engine.complete(task),
            ):
                with self.assertRaisesRegex(Conflict, "Unfinished proposal"):
                    action()
            verify.assert_not_called()

    def test_real_file_replacement_interrupt_preserves_partial_files_and_blocks_adoption(self):
        engine, task, workspace = self.workspace()
        before = snapshot(workspace)
        original = (workspace / "calc.py").read_bytes()
        changes = self.changes(workspace)
        record = interrupt(self, engine, task, workspace, changes, "file")
        self.assertEqual(
            base64.b64decode(record["intent"]["files"][0]["before"]["bytes"]), original
        )
        self.assertIn(b"return a + b", (workspace / "calc.py").read_bytes())
        self.assertFalse((workspace / "new.py").exists())
        self.assertEqual(
            command(["git", "rev-parse", "HEAD"], workspace), record["intent"]["before_head"]
        )
        partial = snapshot(workspace)
        self.assert_blocked(engine, task, workspace)
        with self.assertRaisesRegex(Conflict, "Interrupted proposal preserved"):
            application.recover(engine, task, workspace)
        with self.assertRaises(Conflict):
            engine.apply_changes(task, workspace, changes)
        self.assertEqual(snapshot(workspace), partial)
        # Exact restoration is a checkable recovery boundary, never a blind retry.
        restore_original(workspace, record)
        self.assertEqual(snapshot(workspace), before)
        self.assertIsNone(application.recover(engine, task, workspace))
        self.assertEqual(application.read(engine, task["track"])["phase"], "restored")
        engine.apply_changes(task, workspace, changes)
        self.assertEqual((workspace / "new.py").read_text(), "# 새 파일 🌿\n")

    def test_real_precommit_interrupt_does_not_adopt_all_written_and_staged_files(self):
        engine, task, workspace = self.workspace()
        record = interrupt(self, engine, task, workspace, self.changes(workspace), "before-commit")
        self.assertEqual(record["phase"], "ready")
        self.assertTrue((workspace / "new.py").exists())
        self.assert_blocked(engine, task, workspace)
        before = snapshot(workspace)
        with self.assertRaisesRegex(Conflict, "Interrupted proposal preserved"):
            application.recover(engine, task, workspace)
        self.assertEqual(snapshot(workspace), before)
        restore_original(workspace, record)
        self.assertIsNone(application.recover(engine, task, workspace))

    def test_real_postcommit_interrupt_recovers_exact_commit_without_replaying_worker(self):
        engine, task, workspace = self.workspace()
        changes = self.changes(workspace)
        changes.append(
            {
                "path": "test_calc.py",
                "content": (
                    "import unittest\n"
                    "from calc import add\n\n"
                    "class AdditionTests(unittest.TestCase):\n"
                    "    def test_sum(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n"
                ),
            }
        )
        record = interrupt(self, engine, task, workspace, changes, "after-commit")
        head = command(["git", "rev-parse", "HEAD"], workspace)
        self.assertNotEqual(head, record["intent"]["before_head"])
        self.assert_blocked(engine, task, workspace)
        recovered = Engine(self.s)
        with patch("todo_flow.engine.run_worker") as worker:
            recovered.execute(task)
            worker.assert_not_called()
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
        self.assertEqual(application.read(engine, task["track"])["phase"], "committed")
        self.assertEqual(self.s.track(task["track"])["head"], head)
        verification = json.loads(self.s.track(task["track"])["verification"])
        self.assertTrue(verification["ok"], verification["output"])
        self.assertEqual(verification["head"], head)
        self.assertIn("test_sum", verification["output"])
        self.assertFalse(self.s.snapshot()["decisions"])

    def test_same_legacy_and_edit_proposal_are_idempotent(self):
        engine, task, workspace = self.workspace()
        for changes in (
            self.changes(workspace),
            [{"path": "calc.py", "content": "# Second proposal\n"}],
        ):
            before = command(["git", "rev-parse", "HEAD"], workspace)
            engine.apply_changes(task, workspace, changes, expected_head=before)
            head = command(["git", "rev-parse", "HEAD"], workspace)
            receipt = application.journal_path(engine, task["track"]).read_bytes()
            engine.apply_changes(task, workspace, changes, expected_head=before)
            self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
            self.assertEqual(application.journal_path(engine, task["track"]).read_bytes(), receipt)
        # A no-op produces a receipt without an empty commit.
        engine.apply_changes(task, workspace, changes, expected_head=head)
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
        self.assertFalse(application.read(engine, task["track"])["commit"])

    def test_commit_recovery_rejects_wrong_parent_marker_result_or_changed_paths(self):
        for tamper in ("parent", "marker", "result", "paths"):
            with self.subTest(tamper=tamper):
                case = InterruptedProposalTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    interrupt(
                        case, engine, task, workspace, case.changes(workspace), "after-commit"
                    )
                    if tamper == "parent":
                        command(["git", "commit", "--allow-empty", "-m", "Other commit"], workspace)
                    elif tamper == "marker":
                        command(["git", "commit", "--amend", "-m", "Other message"], workspace)
                    elif tamper == "result":
                        (workspace / "calc.py").write_text("# User edit\n")
                    else:
                        (workspace / "extra.py").write_text("# Unrequested\n")
                        command(["git", "add", "extra.py"], workspace)
                        command(["git", "commit", "--amend", "--no-edit"], workspace)
                    before = snapshot(workspace)
                    with case.assertRaises(Conflict):
                        application.recover(engine, task, workspace)
                    case.assertEqual(snapshot(workspace), before)
                    case.assertEqual(application.read(engine, task["track"])["phase"], "ready")
                finally:
                    case.tearDown()

    def test_unknown_or_broken_journal_blocks_before_any_write(self):
        engine, task, workspace = self.workspace()
        before = snapshot(workspace)
        path = application.journal_path(engine, task["track"])
        for content in ('{"version":99}', '{"version":1', '{"version":1,"phase":"ready"}'):
            path.write_text(content)
            with self.assertRaisesRegex(Conflict, "unsupported proposal journal"):
                engine.apply_changes(task, workspace, self.changes(workspace))
            with self.assertRaises(Conflict):
                engine.ensure_workspace(task)
            with self.assertRaises(Conflict):
                engine.verify(task, workspace)
            self.assertEqual(snapshot(workspace), before)
            self.assertEqual(path.read_text(), content)

    def test_failed_intent_persistence_and_stale_claim_cannot_write(self):
        engine, task, workspace = self.workspace()
        changes = self.changes(workspace)
        before = snapshot(workspace)
        with patch.object(application, "write_json", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                engine.apply_changes(task, workspace, changes)
        self.assertEqual(snapshot(workspace), before)
        interrupt(self, engine, task, workspace, changes, "after-commit")
        preserved = application.journal_path(engine, task["track"]).read_bytes()
        self.s.control(task["track"], "cancel")
        with self.assertRaises(Conflict):
            application.recover(engine, task, workspace)
        self.assertEqual(application.journal_path(engine, task["track"]).read_bytes(), preserved)


class InterruptedRepairTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    ready = test_integration_repair.IntegrationRepairTests.ready
    repair_task = test_integration_repair.IntegrationRepairTests.repair_task

    def test_merge_commit_interrupt_preserves_both_parents_and_resolved_tree(self):
        self.ready()
        task, workspace = self.repair_task()
        repair = integration.prepare(self.e, task, workspace)
        changes = [{"path": "calc.py", "content": test_integration_repair.RESOLVED}]
        record = interrupt(self, self.e, task, workspace, changes, "after-commit", repair)
        head = command(["git", "rev-parse", "HEAD"], workspace)
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [repair["candidate"], repair["base"]],
        )
        pending = integration.pending(self.s.track(task["track"]))
        self.assertEqual(pending["resolved_tree"], record["resolved_tree"])
        wrong = copy.deepcopy(record)
        wrong["resolved_tree"] = "0" * 40
        path = application.journal_path(self.e, task["track"])
        path.write_text(json.dumps(wrong))
        with self.assertRaisesRegex(Conflict, "merge tree changed"):
            application.recover(self.e, task, workspace)
        path.write_text(json.dumps(record))
        with patch("todo_flow.engine.run_worker") as worker:
            Engine(self.s).execute(task)
            worker.assert_not_called()
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
        self.assertIsNone(integration.pending(self.s.track(task["track"])))
        self.assertEqual(
            (workspace / "upstream.txt").read_text(),
            "Upstream addition outside the write surface\n",
        )
        self.assertTrue(json.loads(self.s.track(task["track"])["verification"])["ok"])
        self.assertEqual(
            [row["kind"] for row in self.s.snapshot()["tasks"] if row["status"] == "queued"],
            ["review"],
        )

    def test_precommit_merge_restoration_restores_original_conflict_checkpoint(self):
        self.ready()
        task, workspace = self.repair_task()
        repair = integration.prepare(self.e, task, workspace)
        original = snapshot(workspace)
        changes = [{"path": "calc.py", "content": test_integration_repair.RESOLVED}]
        record = interrupt(self, self.e, task, workspace, changes, "before-commit", repair)
        with self.assertRaises(Conflict):
            application.recover(self.e, task, workspace)
        restore_original(workspace, record)
        self.assertEqual(snapshot(workspace), original)
        self.assertIsNone(application.recover(self.e, task, workspace))
        resumed = integration.prepare(self.e, task, workspace)
        self.assertEqual(resumed["checkout"], repair["checkout"])
        self.assertTrue(integration.unmerged(workspace))


class CompleteWorkerOutputTests(unittest.TestCase):
    def test_claude_stream_requires_complete_unique_final_result(self):
        result = {"type": "result", "structured_output": {"summary": "Complete"}}
        event = json.dumps({"type": "assistant", "message": "Read"})
        final = json.dumps(result)
        self.assertEqual(result_payload(event + "\n" + final + "\n"), result)
        self.assertEqual(result_payload(final), result)
        for output in (
            event + "\n" + final + '\n{"type":"result"',
            event + "\n" + final + "\ngarbage",
            final + "\n" + final,
            final + "\n" + event,
            event + "\n" + final[:-1],
        ):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    result_payload(output)

    def test_command_adapter_requires_one_entire_json_document(self):
        valid = json.dumps({"summary": "Complete"})
        wrapped = json.dumps({"type": "result", "structured_output": {"summary": "Earlier"}})
        for output in (
            valid,
            valid + '\n{"summary":',
            wrapped + '\n{"type":"result"',
            "{}\n" + wrapped,
        ):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = {
                    "worker_protocol": 2,
                    "worker_launcher": "headless",
                    "worker": {
                        "type": "command",
                        "argv": [sys.executable, "-c", f"print({output!r})"],
                    },
                }
                args = (
                    config,
                    {"workspace": str(root)},
                    {"attempt": "response", "kind": "work"},
                    root / "state",
                    lambda _: None,
                )
                if output == valid:
                    self.assertEqual(run_worker(*args)["summary"], "Complete")
                else:
                    with self.assertRaises(ValueError):
                        run_worker(*args)
