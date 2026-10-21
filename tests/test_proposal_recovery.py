import base64
import copy
import json
import os
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
import test_managed_workspace
from todo_flow import integration, proposal_application as application
from todo_flow.adapters import command
from todo_flow.checkout import snapshot
from todo_flow.engine import Engine
from todo_flow.store import Conflict, fingerprint
from todo_flow.worker import result_payload, run_worker
from todo_flow.workspace_creation import WorkspaceCreationBlocked


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
original_atomic_file = application.atomic_file

def die():
    os.kill(os.getpid(), signal.SIGKILL)

def atomic_file(*args, **kwargs):
    if data["boundary"] == "before-files":
        die()
    return original_atomic_file(*args, **kwargs)

def replace(source, target):
    original_replace(source, target)
    if Path(target) == workspace / data["changes"][0]["path"] and data["boundary"] == "file":
        record = application.read(engine, data["task"]["track"])
        assert record["phase"] == "applying"
        assert record["intent"]["files"][0]["before"] is not None
        die()
    if Path(target) == workspace / data["changes"][-1]["path"] and data["boundary"] == "after-files":
        die()

def command(argv, *args, **kwargs):
    if argv[:2] == ["git", "commit"] and data["boundary"] == "before-commit":
        die()
    result = original_command(argv, *args, **kwargs)
    if argv[:2] == ["git", "add"] and data["boundary"] == "after-stage":
        die()
    if argv[:2] == ["git", "commit"] and data["boundary"] == "after-commit":
        die()
    return result

with file_lock(engine.store.path / "locks" / (data["task"]["track"] + ".lock")):
    with (
        patch.object(application.os, "replace", replace),
        patch.object(application, "command", command),
        patch.object(application, "atomic_file", atomic_file),
    ):
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


def workspace_evidence(workspace):
    files = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if relative.parts[0] == ".git":
            continue
        metadata = path.lstat()
        if path.is_symlink():
            content = ("symlink", os.readlink(path))
        elif path.is_file():
            content = ("file", path.read_bytes())
        else:
            content = ("directory", None)
        files[str(relative)] = (metadata.st_mode & 0o777, content)
    index = Path(command(["git", "rev-parse", "--git-path", "index"], workspace))
    if not index.is_absolute():
        index = workspace / index
    merge_head = Path(command(["git", "rev-parse", "--git-path", "MERGE_HEAD"], workspace))
    if not merge_head.is_absolute():
        merge_head = workspace / merge_head
    return {
        "files": files,
        "head": command(["git", "rev-parse", "HEAD"], workspace),
        "index": index.read_bytes() if index.exists() else None,
        "merge_head": merge_head.read_bytes() if merge_head.exists() else None,
    }


def assert_retry_reuses_receipt(case, engine, task, workspace, changes, before, repair=None):
    head = command(["git", "rev-parse", "HEAD"], workspace)
    path = application.journal_path(engine, task["track"])
    receipt = path.read_bytes()
    # same-context-retry: adoption is exactly once, including after an Engine restart.
    for retry in (engine, Engine(engine.store)):
        with (
            patch.object(application, "atomic_file") as write,
            patch.object(application, "write_json") as save,
            patch.object(application, "command", wraps=command) as git,
        ):
            retry.apply_changes(task, workspace, changes, repair, expected_head=before)
            write.assert_not_called()
            save.assert_not_called()
            case.assertFalse(
                any(
                    call.args[0][:2] in (["git", "add"], ["git", "commit"])
                    for call in git.call_args_list
                )
            )
        case.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
        case.assertEqual(path.read_bytes(), receipt)


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

    def test_real_precommit_interrupt_adopts_exact_staged_after_once(self):
        engine, task, workspace = self.workspace()
        changes = self.changes(workspace)
        record = interrupt(self, engine, task, workspace, changes, "before-commit")
        self.assertEqual(record["phase"], "ready")
        self.assertTrue((workspace / "new.py").exists())
        self.assert_blocked(engine, task, workspace)
        # journal-cross-task authorizes exact after recovery within the same scope.
        # Previously even this fully staged checkpoint always raised Conflict.
        recovered = application.recover(engine, task, workspace)
        self.assertEqual(recovered["phase"], "committed")
        self.assertEqual(
            command(
                ["git", "rev-list", "--count", record["intent"]["before_head"] + "..HEAD"],
                workspace,
            ),
            "1",
        )
        assert_retry_reuses_receipt(
            self, engine, task, workspace, changes, record["intent"]["before_head"]
        )

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

    def test_completed_application_reuses_only_a_stored_worker_result(self):
        # journal-cross-task must preserve both normal worker execution after
        # result-less setup and replay of the original task's stored result.
        for with_result in (False, True):
            with self.subTest(with_result=with_result):
                case = InterruptedProposalTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    changes = case.changes(workspace)
                    result = {"summary": "Preserved proposal", "changes": changes}
                    engine.apply_changes(
                        task, workspace, changes, result=result if with_result else None
                    )
                    before = snapshot(workspace)
                    path = application.journal_path(engine, task["track"])
                    receipt = path.read_bytes()
                    for retry in (engine, Engine(case.s)):
                        recovered = application.recover(retry, task, workspace)
                        if with_result:
                            case.assertEqual(recovered["intent"]["result"], result)
                        else:
                            case.assertIsNone(recovered)
                        case.assertEqual(snapshot(workspace), before)
                        case.assertEqual(path.read_bytes(), receipt)
                finally:
                    case.tearDown()

    def test_same_legacy_and_edit_proposal_are_idempotent(self):
        engine, task, workspace = self.workspace()
        for changes in (
            self.changes(workspace),
            [{"path": "calc.py", "content": "# Second proposal\n"}],
        ):
            before = command(["git", "rev-parse", "HEAD"], workspace)
            engine.apply_changes(task, workspace, changes, expected_head=before)
            head = command(["git", "rev-parse", "HEAD"], workspace)
            assert_retry_reuses_receipt(self, engine, task, workspace, changes, before)
        # A no-op produces a receipt without an empty commit.
        engine.apply_changes(task, workspace, changes, expected_head=head)
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
        self.assertFalse(application.read(engine, task["track"])["commit"])
        assert_retry_reuses_receipt(self, engine, task, workspace, changes, head)

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


class SuccessorRecoveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    workspace = test_change_proposals.ProposalApplicationTests.workspace
    changes = InterruptedProposalTests.changes

    def successor(self, task, *, request=False, revision=False):
        original_request = self.s.track(task["track"])["request"]
        self.s.finish(task, {"summary": "Original task ended"})
        if revision:
            self.s.control(task["track"], "pause")
            self.s.register({**test_flow.DOC, "goal": "Read-only inspection"}, 1)
        elif request:
            self.s.control(task["track"], "cancel")
        if request or revision:
            self.s.start(task["track"], None if request else original_request)
        # A new request enqueues assess; finish it without changing the journal.
        if request:
            assess = self.s.claim("successor-assessor")
            self.s.finish(assess, {"summary": "Inspect interrupted proposal"})
        with self.s.transaction() as connection:
            self.s.enqueue(connection, task["track"], "work", "Recover proposal", "successor")
        return self.s.claim("successor")

    def legacy(self, engine, task):
        record = application.read(engine, task["track"])
        for field in ("scope", "outside", "index_entries"):
            record["intent"].pop(field)
        record["id"] = fingerprint(record["intent"])
        application.journal_path(engine, task["track"]).write_text(json.dumps(record))

    def preserved(self, engine, task, workspace):
        checkout = snapshot(workspace)
        index = Path(command(["git", "rev-parse", "--git-path", "index"], workspace))
        if not index.is_absolute():
            index = workspace / index
        return (
            checkout,
            index.read_bytes(),
            application.journal_path(engine, task["track"]).read_bytes(),
            [(path.name, application.file_state(path)) for path in sorted(workspace.glob("*.py"))],
        )

    def sparse_workspace(self):
        for name, content in (
            ("included/keep.py", "# included\n"),
            ("excluded/target.py", "value = 1\n"),
        ):
            path = self.repo / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "Add sparse proposal fixture"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        engine, task, workspace = self.workspace()
        command(["git", "sparse-checkout", "init", "--cone"], workspace)
        command(["git", "sparse-checkout", "set", "included"], workspace)
        self.assertFalse((workspace / "excluded/target.py").exists())
        self.assertEqual(
            command(["git", "show", "HEAD:excluded/target.py"], workspace), "value = 1"
        )
        # journal-exact-state: unrelated staged, unstaged and untracked work survives.
        (workspace / "included/keep.py").write_text("# staged user edit\n")
        command(["git", "add", "--", "included/keep.py"], workspace)
        (workspace / "included/keep.py").write_text("# unstaged user edit\n")
        (workspace / "included/notes.txt").write_text("Preserve user notes\n")
        return engine, task, workspace

    def assert_sparse_commit(self, engine, task, workspace, before, outside):
        record = application.read(engine, task["track"])
        head = command(["git", "rev-parse", "HEAD"], workspace)
        self.assertEqual(record["phase"], "committed")
        self.assertEqual(record["head"], head)
        self.assertEqual(engine.store.track(task["track"])["head"], head)
        self.assertEqual(record["adopted_by"], {"task": task["id"], "attempt": task["attempt"]})
        self.assertEqual(command(["git", "rev-list", "--count", before + "..HEAD"], workspace), "1")
        self.assertEqual(command(["git", "show", "-s", "--format=%P", "HEAD"], workspace), before)
        self.assertEqual(
            command(["git", "diff", "--name-only", before, "HEAD"], workspace),
            "excluded/target.py",
        )
        committed = subprocess.run(
            ["git", "show", "HEAD:excluded/target.py"],
            cwd=workspace,
            capture_output=True,
            check=True,
        )
        self.assertEqual(committed.stdout, b"value = 2\n")
        self.assertEqual((workspace / "excluded/target.py").read_bytes(), b"value = 2\n")
        self.assertEqual(
            application.outside_state(workspace, [{"path": "excluded/target.py"}]), outside
        )
        self.assertEqual(
            command(["git", "show", ":included/keep.py"], workspace), "# staged user edit"
        )
        self.assertEqual((workspace / "included/keep.py").read_bytes(), b"# unstaged user edit\n")
        self.assertEqual((workspace / "included/notes.txt").read_bytes(), b"Preserve user notes\n")

    def test_normal_apply_commits_tracked_target_outside_sparse_cone_once(self):
        # decision-aa71971824c7438b: normal apply must retain its one-commit contract.
        engine, task, workspace = self.sparse_workspace()
        before = command(["git", "rev-parse", "HEAD"], workspace)
        changes = [{"path": "excluded/target.py", "content": "value = 2\n"}]
        outside = application.outside_state(workspace, changes)
        engine.apply_changes(task, workspace, changes, expected_head=before)
        self.assert_sparse_commit(engine, task, workspace, before, outside)
        assert_retry_reuses_receipt(self, engine, task, workspace, changes, before)
        self.assert_sparse_commit(engine, task, workspace, before, outside)

    def test_successor_recovers_exact_after_outside_sparse_cone_once(self):
        # journal-cross-task: exact after under the same request/revision is adopted once.
        engine, task, workspace = self.sparse_workspace()
        before = command(["git", "rev-parse", "HEAD"], workspace)
        index_before = command(["git", "ls-files", "--stage", "-z"], workspace)
        changes = [{"path": "excluded/target.py", "content": "value = 2\n"}]
        outside = application.outside_state(workspace, changes)
        record = interrupt(self, engine, task, workspace, changes, "after-files")
        self.assertEqual(record["phase"], "applying")
        self.assertEqual(record["intent"]["before_head"], before)
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), before)
        self.assertEqual(command(["git", "ls-files", "--stage", "-z"], workspace), index_before)
        self.assertEqual(command(["git", "show", ":excluded/target.py"], workspace), "value = 1")
        for row in record["intent"]["files"]:
            self.assertIsNone(row["before"])
            self.assertEqual(application.file_state(workspace / row["path"]), row["after"])
        successor = self.successor(task)
        self.assertNotEqual(successor["id"], task["id"])
        self.assertEqual(successor["input_revision"], record["intent"]["scope"]["revision"])
        self.assertEqual(
            self.s.track(task["track"])["request"], record["intent"]["scope"]["request"]
        )
        recovered = application.recover(engine, successor, workspace)
        self.assertEqual(
            recovered["recovered_by"], {"task": successor["id"], "attempt": successor["attempt"]}
        )
        self.assert_sparse_commit(engine, successor, workspace, before, outside)
        for retry in (engine, Engine(self.s)):
            self.assertEqual(application.recover(retry, successor, workspace), recovered)
        assert_retry_reuses_receipt(self, engine, successor, workspace, changes, before)
        self.assert_sparse_commit(engine, successor, workspace, before, outside)

    def test_successor_releases_exact_before_without_applying_old_result(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                case = SuccessorRecoveryTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    before = snapshot(workspace)
                    interrupt(
                        case, engine, task, workspace, case.changes(workspace), "before-files"
                    )
                    if legacy:
                        case.legacy(engine, task)
                    successor = case.successor(task)
                    case.assertEqual(engine.ensure_workspace(successor), workspace)
                    for retry in (engine, Engine(case.s)):
                        case.assertIsNone(application.recover(retry, successor, workspace))
                    case.assertEqual(application.read(engine, task["track"])["phase"], "restored")
                    case.assertEqual(snapshot(workspace), before)
                    case.assertFalse((workspace / "new.py").exists())
                finally:
                    case.tearDown()

    def test_successor_adopts_exact_after_once_and_engine_verifies_without_worker(self):
        scenarios = [
            (boundary, False)
            for boundary in ("after-files", "after-stage", "before-commit", "after-commit")
        ] + [("after-files", True), ("after-stage", True)]
        for boundary, legacy in scenarios:
            with self.subTest(boundary=boundary, legacy=legacy):
                case = SuccessorRecoveryTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    changes = case.changes(workspace)
                    changes.append(
                        {
                            "path": "test_calc.py",
                            "content": (
                                "import unittest\nfrom calc import add\n\n"
                                "class AdditionTests(unittest.TestCase):\n"
                                "    def test_sum(self):\n"
                                "        self.assertEqual(add(2, 3), 5)\n"
                            ),
                        }
                    )
                    record = interrupt(case, engine, task, workspace, changes, boundary)
                    if legacy:
                        case.legacy(engine, task)
                    successor = case.successor(task)
                    case.assertEqual(engine.ensure_workspace(successor), workspace)
                    recovered = application.recover(engine, successor, workspace)
                    case.assertEqual(recovered["phase"], "committed")
                    head = recovered["head"]
                    case.assertEqual(
                        command(
                            [
                                "git",
                                "rev-list",
                                "--count",
                                record["intent"]["before_head"] + "..HEAD",
                            ],
                            workspace,
                        ),
                        "1",
                    )
                    assert_retry_reuses_receipt(
                        case, engine, successor, workspace, changes, record["intent"]["before_head"]
                    )
                    with patch("todo_flow.engine.run_worker") as worker:
                        Engine(case.s).execute(successor)
                        worker.assert_not_called()
                    verification = json.loads(case.s.track(task["track"])["verification"])
                    case.assertTrue(verification["ok"], verification["output"])
                    case.assertEqual(verification["head"], head)
                    case.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), head)
                    case.assertFalse(case.s.snapshot()["decisions"])
                finally:
                    case.tearDown()

    def test_decision_answer_assess_successor_can_recover_before_choosing_more_work(self):
        engine, task, workspace = self.workspace()
        changes = self.changes(workspace)
        changes.append(
            {
                "path": "test_calc.py",
                "content": (
                    "import unittest\nfrom calc import add\n\n"
                    "class AdditionTests(unittest.TestCase):\n"
                    "    def test_sum(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n"
                ),
            }
        )
        record = interrupt(self, engine, task, workspace, changes, "after-stage")
        engine.fail(task, RuntimeError("Interrupted proposal"))
        decision = self.s.snapshot()["decisions"][0]
        self.s.answer(decision["id"], "Resume the same request and revision")
        successor = self.s.claim("successor-assessor")
        self.assertEqual(successor["kind"], "assess")
        # Store.answer resumes ordinary failures with assess, not work.
        # This host recovery must happen before require_clear could block that path.
        with patch("todo_flow.engine.run_worker") as worker:
            Engine(self.s).execute(successor)
            worker.assert_not_called()
        self.assertEqual(application.read(engine, task["track"])["phase"], "committed")
        self.assertEqual(
            command(
                ["git", "rev-list", "--count", record["intent"]["before_head"] + "..HEAD"],
                workspace,
            ),
            "1",
        )
        verification = json.loads(self.s.track(task["track"])["verification"])
        self.assertTrue(verification["ok"], verification["output"])
        self.assertFalse(any(row["status"] == "open" for row in self.s.snapshot()["decisions"]))

    def test_request_revision_mismatch_preserves_checkout_index_and_journal(self):
        # journal-scope-fence: a read-only revision cannot authorize old writes.
        for request, revision in ((True, False), (False, True), (True, True)):
            for boundary, legacy in (
                ("after-stage", False),
                ("after-stage", True),
                ("after-commit", False),
            ):
                with self.subTest(
                    request=request, revision=revision, boundary=boundary, legacy=legacy
                ):
                    case = SuccessorRecoveryTests()
                    case.setUp()
                    try:
                        engine, task, workspace = case.workspace()
                        changes = case.changes(workspace)
                        record = interrupt(case, engine, task, workspace, changes, boundary)
                        if legacy:
                            case.legacy(engine, task)
                        successor = case.successor(task, request=request, revision=revision)
                        # Mutable task input is not proof of the original attempt's revision.
                        if revision:
                            with case.s.transaction() as connection:
                                connection.execute(
                                    "UPDATE tasks SET input_revision=? WHERE id=?",
                                    (successor["input_revision"], task["id"]),
                                )
                        before = case.preserved(engine, task, workspace)
                        with (
                            patch.object(application, "atomic_file") as write,
                            patch.object(application, "write_json") as save,
                            patch.object(application, "command", wraps=command) as git,
                        ):
                            for action in (
                                lambda: engine.ensure_workspace(successor),
                                lambda: application.recover(engine, successor, workspace),
                                lambda: engine.apply_changes(
                                    successor,
                                    workspace,
                                    changes,
                                    expected_head=record["intent"]["before_head"],
                                ),
                            ):
                                with case.assertRaisesRegex(Conflict, "request/revision mismatch"):
                                    action()
                            write.assert_not_called()
                            save.assert_not_called()
                            case.assertFalse(
                                any(
                                    call.args[0][:2] in (["git", "add"], ["git", "commit"])
                                    for call in git.call_args_list
                                )
                            )
                        case.assertEqual(case.preserved(engine, task, workspace), before)
                    finally:
                        case.tearDown()

    def test_recovery_rejects_29_adversarial_states_without_mutating_evidence(self):
        variants = [
            "target-bytes",
            "target-mode",
            "target-missing",
            "target-directory",
            "new-file-missing",
            "untracked-simple",
            "untracked-nested",
            "untracked-case",
            "untracked-unicode-nfc",
            "untracked-unicode-nfd",
            "untracked-newline",
            "untracked-tab",
            "untracked-leading-space",
            "untracked-trailing-space",
            "untracked-emoji",
            "untracked-hidden",
            "untracked-long-name",
            "untracked-symlink",
            "unrelated-worktree-edit",
            "partial-worktree-index-after",
            "index-empty",
            "index-target-unexpected",
            "head-generation-change",
            "merge-marker-change",
            "journal-fingerprint-change",
            "journal-shape-change",
            "journal-scope-mismatch",
            "journal-generation-type",
            "file-read-exception",
        ]
        self.assertGreaterEqual(len(variants), 20)
        for variant in variants:
            with self.subTest(counterexample=variant):
                case = SuccessorRecoveryTests()
                case.setUp()
                try:
                    engine, old_task, workspace = case.workspace()
                    record = interrupt(
                        case, engine, old_task, workspace, case.changes(workspace), "after-stage"
                    )
                    journal_path = application.journal_path(engine, old_task["track"])
                    if variant == "target-bytes":
                        (workspace / "calc.py").write_text("changed\n")
                    elif variant == "target-mode":
                        (workspace / "calc.py").chmod(0o755)
                    elif variant == "target-missing":
                        (workspace / "calc.py").unlink()
                    elif variant == "target-directory":
                        target = workspace / "calc.py"
                        target.unlink()
                        target.mkdir()
                        (target / "nested.txt").write_text("preserve nested data\n")
                    elif variant == "new-file-missing":
                        (workspace / "new.py").unlink()
                    elif variant.startswith("untracked-"):
                        names = {
                            "untracked-simple": "extra.txt",
                            "untracked-nested": "extra/nested/file.txt",
                            "untracked-case": "ExtraCase.TXT",
                            "untracked-unicode-nfc": "café.txt",
                            "untracked-unicode-nfd": "cafe\u0301.txt",
                            "untracked-newline": "line\nbreak.txt",
                            "untracked-tab": "tab\tname.txt",
                            "untracked-leading-space": " leading.txt",
                            "untracked-trailing-space": "trailing .txt",
                            "untracked-emoji": "🌱.txt",
                            "untracked-hidden": ".extra",
                            "untracked-long-name": "x" * 100 + ".txt",
                            "untracked-symlink": "extra-link",
                        }
                        extra = workspace / names[variant]
                        extra.parent.mkdir(parents=True, exist_ok=True)
                        if variant == "untracked-symlink":
                            extra.symlink_to("missing-target")
                        else:
                            extra.write_text("later work\n")
                    elif variant == "unrelated-worktree-edit":
                        (workspace / "calc.py.copy").write_text("unrelated later file\n")
                    elif variant == "partial-worktree-index-after":
                        row = record["intent"]["files"][-1]
                        target = workspace / row["path"]
                        if row["before"] is None:
                            target.unlink()
                        else:
                            target.write_bytes(base64.b64decode(row["before"]["bytes"]))
                            target.chmod(row["before"]["mode"])
                    elif variant == "index-empty":
                        index = Path(
                            command(["git", "rev-parse", "--git-path", "index"], workspace)
                        )
                        if not index.is_absolute():
                            index = workspace / index
                        index.write_bytes(b"invalid index")
                    elif variant == "index-target-unexpected":
                        row = record["intent"]["files"][0]
                        target = workspace / row["path"]
                        target.write_text("unexpected staged content\n")
                        command(["git", "add", "--", ":(literal)" + row["path"]], workspace)
                        target.write_bytes(base64.b64decode(row["after"]["bytes"]))
                        target.chmod(row["after"]["mode"])
                    elif variant == "head-generation-change":
                        command(
                            ["git", "commit", "--allow-empty", "-m", "later generation"], workspace
                        )
                    elif variant == "merge-marker-change":
                        git_dir = Path(
                            command(["git", "rev-parse", "--absolute-git-dir"], workspace)
                        )
                        head = command(["git", "rev-parse", "HEAD"], workspace)
                        (git_dir / "MERGE_HEAD").write_text(head + "\n")
                    elif variant in (
                        "journal-fingerprint-change",
                        "journal-shape-change",
                        "journal-scope-mismatch",
                        "journal-generation-type",
                    ):
                        payload = json.loads(journal_path.read_text())
                        if variant == "journal-fingerprint-change":
                            payload["id"] = "0" * 64
                        elif variant == "journal-shape-change":
                            payload["intent"]["files"] = {"path": "not-a-list"}
                        elif variant == "journal-scope-mismatch":
                            payload["intent"]["scope"]["request"] = "stale-request"
                            payload["id"] = fingerprint(payload["intent"])
                        else:
                            payload["intent"]["generation"] = "1"
                            payload["id"] = fingerprint(payload["intent"])
                        journal_path.write_text(json.dumps(payload))

                    successor = case.successor(old_task)
                    before_workspace = workspace_evidence(workspace)
                    before_journal = journal_path.read_bytes()
                    if variant == "file-read-exception":
                        with patch.object(
                            application, "file_state", side_effect=OSError("injected read failure")
                        ):
                            with self.assertRaises(Conflict):
                                application.recover(engine, successor, workspace)
                    else:
                        with self.assertRaises(Conflict):
                            application.recover(engine, successor, workspace)
                    self.assertEqual(workspace_evidence(workspace), before_workspace)
                    self.assertEqual(journal_path.read_bytes(), before_journal)
                    self.assertEqual(record["phase"], "applying")
                finally:
                    case.tearDown()

    def test_concurrent_checkout_write_during_recovery_stays_preserved_and_held(self):
        case = SuccessorRecoveryTests()
        case.setUp()
        try:
            engine, task, workspace = case.workspace()
            interrupt(case, engine, task, workspace, case.changes(workspace), "after-stage")
            successor = case.successor(task)
            journal = application.journal_path(engine, task["track"]).read_bytes()
            before_head = command(["git", "rev-parse", "HEAD"], workspace)
            extra = workspace / "operator" / "concurrent-note.txt"
            original = application.require_after

            def concurrent_write(root, record):
                extra.parent.mkdir(parents=True)
                extra.write_text("concurrent user change\n")
                return original(root, record)

            with patch.object(application, "require_after", side_effect=concurrent_write):
                with self.assertRaisesRegex(Conflict, "unrelated checkout/index changed"):
                    application.recover(engine, successor, workspace)

            self.assertEqual(extra.read_text(), "concurrent user change\n")
            self.assertEqual(application.journal_path(engine, task["track"]).read_bytes(), journal)
            self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), before_head)
        finally:
            case.tearDown()

    def test_successor_preserves_partial_and_tampered_states(self):
        # journal-exact-state: scope validity never licenses overwriting user state.
        for tamper in (
            "partial",
            "file",
            "mode",
            "index",
            "head",
            "outside",
            "claim",
            "origin",
            "running",
        ):
            with self.subTest(tamper=tamper):
                case = SuccessorRecoveryTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    interrupt(
                        case,
                        engine,
                        task,
                        workspace,
                        case.changes(workspace),
                        "file" if tamper == "partial" else "after-stage",
                    )
                    successor = case.successor(task)
                    if tamper == "file":
                        (workspace / "calc.py").write_text("# User edit\n")
                    elif tamper == "mode":
                        (workspace / "calc.py").chmod(0o755)
                    elif tamper == "index":
                        command(["git", "reset", "HEAD", "--", "new.py"], workspace)
                    elif tamper == "head":
                        command(["git", "commit", "--allow-empty", "-m", "Other commit"], workspace)
                    elif tamper == "outside":
                        (workspace / "operator.txt").write_text("Preserve user data\n")
                        command(["git", "add", "operator.txt"], workspace)
                    elif tamper == "claim":
                        case.s.control(task["track"], "cancel")
                    elif tamper == "running":
                        with case.s.transaction() as connection:
                            connection.execute(
                                "UPDATE attempts SET status='running' WHERE id=?",
                                (task["attempt"],),
                            )
                    elif tamper == "origin":
                        case.legacy(engine, task)
                        with case.s.transaction() as connection:
                            for row in connection.execute(
                                "SELECT seq,body FROM events WHERE type='worker.claimed'"
                            ).fetchall():
                                if json.loads(row["body"])["attemptId"] == task["attempt"]:
                                    connection.execute(
                                        "DELETE FROM events WHERE seq=?", (row["seq"],)
                                    )
                    before = case.preserved(engine, task, workspace)
                    with (
                        patch.object(application, "atomic_file") as write,
                        patch.object(application, "write_json") as save,
                    ):
                        with case.assertRaises(Conflict):
                            application.recover(engine, successor, workspace)
                        write.assert_not_called()
                        save.assert_not_called()
                    case.assertEqual(case.preserved(engine, task, workspace), before)
                finally:
                    case.tearDown()


class InterruptedRepairTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    ready = test_integration_repair.IntegrationRepairTests.ready
    repair_task = test_integration_repair.IntegrationRepairTests.repair_task

    def prepare_merge_after_noop(self):
        self.ready(conflict=False)
        task, workspace = self.repair_task()
        head = self.before["head"]
        self.e.apply_changes(task, workspace, [], expected_head=head)
        prior = application.read(self.e, task["track"])
        self.assertEqual(prior["phase"], "committed")
        self.assertFalse(prior["commit"])
        self.assertIsNone(prior["intent"]["merge_head"])
        self.assertEqual(prior["head"], head)
        repair = integration.prepare(self.e, task, workspace)
        self.assertEqual(repair["conflicts"], [])
        self.assertEqual(integration.merge_head(workspace), self.base)
        return task, workspace, repair, prior

    def test_new_merge_after_empty_receipt_commits_both_parents_and_retries_once(self):
        task, workspace, repair, prior = self.prepare_merge_after_noop()
        # merge-context-distinct: the same [] at H must adopt the new H/B merge.
        # Before the fix this raises "Proposal still has unresolved merge state".
        self.e.apply_changes(task, workspace, [], repair, expected_head=prior["head"])
        head = command(["git", "rev-parse", "HEAD"], workspace)
        receipt = application.read(self.e, task["track"])
        self.assertNotEqual(head, prior["head"])
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [prior["head"], self.base],
        )
        self.assertEqual(receipt["phase"], "committed")
        self.assertTrue(receipt["commit"])
        self.assertNotEqual(receipt["id"], prior["id"])
        self.assertEqual(receipt["head"], head)
        self.assertEqual(receipt["intent"]["proposal"], [])
        self.assertEqual(receipt["intent"]["merge_head"], self.base)
        self.assertIsNone(integration.merge_head(workspace))
        self.assertEqual(
            (workspace / "upstream.txt").read_text(),
            "Upstream addition outside the write surface\n",
        )
        # A completed merge has no MERGE_HEAD, but is still the same adopted proposal.
        assert_retry_reuses_receipt(self, self.e, task, workspace, [], prior["head"], repair)
        integration.finish_repair(self.e, task, workspace, repair)
        track = self.s.track(task["track"])
        self.assertEqual(track["head"], head)
        self.assertIsNone(track["landing"])
        self.assertIsNone(track["verification"])
        self.assertIsNone(track["review"])

    def test_new_merge_after_empty_receipt_preserves_changed_checkout_and_stale_claim(self):
        # merge-adoption-fences: skipping reuse must still validate the prepared merge.
        for tamper, message in (
            ("file", "checkout/index changed"),
            ("index", "checkout/index changed"),
            ("parent", "merge changed"),
            ("head", "Proposal HEAD changed"),
            ("claim", "Stale claim"),
        ):
            with self.subTest(tamper=tamper):
                case = InterruptedRepairTests()
                case.setUp()
                try:
                    task, workspace, repair, prior = case.prepare_merge_after_noop()
                    path = application.journal_path(case.e, task["track"])
                    receipt = path.read_bytes()
                    if tamper == "file":
                        (workspace / "calc.py").write_text("# User edit\n")
                    elif tamper == "index":
                        command(["git", "reset", "HEAD", "--", "upstream.txt"], workspace)
                    elif tamper == "parent":
                        gitdir = Path(
                            command(["git", "rev-parse", "--absolute-git-dir"], workspace)
                        )
                        (gitdir / "MERGE_HEAD").write_text(prior["head"] + "\n")
                    elif tamper == "head":
                        command(["git", "update-ref", "HEAD", case.base], workspace)
                    else:
                        case.s.control(task["track"], "cancel")
                    before = snapshot(workspace)
                    merging = integration.merge_head(workspace)
                    landing = case.s.track(task["track"])["landing"]
                    with (
                        patch.object(application, "atomic_file") as write,
                        patch.object(application, "write_json") as save,
                        patch.object(application, "command", wraps=command) as git,
                    ):
                        with case.assertRaisesRegex(Conflict, message):
                            case.e.apply_changes(
                                task, workspace, [], repair, expected_head=prior["head"]
                            )
                        write.assert_not_called()
                        save.assert_not_called()
                        case.assertFalse(
                            any(
                                call.args[0][:2] in (["git", "add"], ["git", "commit"])
                                for call in git.call_args_list
                            )
                        )
                    case.assertEqual(snapshot(workspace), before)
                    case.assertEqual(integration.merge_head(workspace), merging)
                    case.assertEqual(path.read_bytes(), receipt)
                    case.assertEqual(case.s.track(task["track"])["landing"], landing)
                finally:
                    case.tearDown()

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

    def test_successor_recovers_exact_staged_merge_and_keeps_both_parents(self):
        self.ready()
        task, workspace = self.repair_task()
        repair = integration.prepare(self.e, task, workspace)
        changes = [{"path": "calc.py", "content": test_integration_repair.RESOLVED}]
        interrupt(self, self.e, task, workspace, changes, "before-commit", repair)
        self.s.finish(
            task,
            {
                "summary": "Interrupted repair released",
                "next": [{"kind": "work", "purpose": "Recover the same merge"}],
            },
        )
        successor = self.s.claim("successor")
        # journal-cross-task also preserves the existing integration-repair contract.
        with patch("todo_flow.engine.run_worker") as worker:
            Engine(self.s).execute(successor)
            worker.assert_not_called()
        head = command(["git", "rev-parse", "HEAD"], workspace)
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [repair["candidate"], repair["base"]],
        )
        self.assertEqual(application.read(self.e, task["track"])["phase"], "committed")
        self.assertIsNone(integration.pending(self.s.track(task["track"])))
        verification = json.loads(self.s.track(task["track"])["verification"])
        self.assertTrue(verification["ok"], verification["output"])
        self.assertEqual(verification["head"], head)
        self.assertFalse(self.s.snapshot()["decisions"])

    def test_precommit_merge_restoration_restores_original_conflict_checkpoint(self):
        self.ready()
        task, workspace = self.repair_task()
        repair = integration.prepare(self.e, task, workspace)
        original = snapshot(workspace)
        changes = [{"path": "calc.py", "content": test_integration_repair.RESOLVED}]
        record = interrupt(self, self.e, task, workspace, changes, "before-commit", repair)
        # Exact after is now recoverable; a user edit must still hold the merge.
        (workspace / "calc.py").write_text("# User edit after interruption\n")
        with self.assertRaises(Conflict):
            application.recover(self.e, task, workspace)
        restore_original(workspace, record)
        self.assertEqual(snapshot(workspace), original)
        self.assertIsNone(application.recover(self.e, task, workspace))
        resumed = integration.prepare(self.e, task, workspace)
        self.assertEqual(resumed["checkout"], repair["checkout"])
        self.assertTrue(integration.unmerged(workspace))


class ManagedProposalRecoveryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    start = test_managed_workspace.ManagedWorkspaceTests.start
    call = test_managed_workspace.ManagedWorkspaceTests.call

    def test_interrupted_commit_checks_live_ownership_before_adoption(self):
        self.start()
        workspace = self.engine.ensure_workspace(self.task)
        before = self.s.track("addition")["head"]
        changes = [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}]
        interrupt(self, self.engine, self.task, workspace, changes, "after-commit")
        self.s.finish(
            self.task,
            {
                "summary": "Original task ended",
                "next": [{"kind": "work", "purpose": "Recover managed proposal"}],
            },
        )
        successor = self.s.claim("successor")
        receipt = application.journal_path(self.engine, "addition").read_bytes()
        identity = self.shown["result"]["worktree"]["instanceId"]
        self.shown["result"]["worktree"]["instanceId"] = "replaced-instance"
        with self.assertRaises(WorkspaceCreationBlocked):
            self.engine.ensure_workspace(successor)
        self.assertEqual(application.journal_path(self.engine, "addition").read_bytes(), receipt)
        self.assertEqual(self.s.track("addition")["head"], before)
        self.shown["result"]["worktree"]["instanceId"] = identity
        # journal-cross-task and journal-exact-state: a stale catalog may converge,
        # but only journal recovery can adopt the interrupted commit's HEAD.
        observations = []

        def lagging(cli, args, root):
            result = self.call(cli, args, root)
            if args[:2] == ["worktree", "show"]:
                worktree = result["result"]["worktree"]
                observations.append(worktree["head"])
                if len(observations) == 1:
                    worktree["head"] = worktree["git"]["head"] = before
                self.assertEqual(self.s.track("addition")["head"], before)
                self.assertEqual(
                    application.journal_path(self.engine, "addition").read_bytes(), receipt
                )
            return result

        with (
            patch("todo_flow.managed_workspace._call", side_effect=lagging),
            patch("todo_flow.managed_workspace.monotonic", return_value=0.0),
            patch("todo_flow.managed_workspace.sleep") as sleep,
        ):
            self.assertEqual(self.engine.ensure_workspace(successor), workspace)
        self.assertEqual(len(observations), 2)
        sleep.assert_called_once_with(1.0)
        self.assertEqual(application.journal_path(self.engine, "addition").read_bytes(), receipt)
        self.assertEqual(self.s.track("addition")["head"], before)
        recovered = application.recover(self.engine, successor, workspace)
        self.assertEqual(recovered["phase"], "committed")
        self.assertEqual(self.s.track("addition")["head"], recovered["head"])
        self.assertEqual(self.created, 1)


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
