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
original_write_json = application.write_json

def die():
    os.kill(os.getpid(), signal.SIGKILL)

def replace(source, target):
    original_replace(source, target)
    if Path(target) == workspace / data["changes"][0]["path"] and data["boundary"] == "file":
        record = application.read(engine, data["task"]["track"])
        assert record["phase"] == "applying"
        assert record["intent"]["files"][0]["before"] is not None
        die()
    if Path(target) == workspace / data["changes"][-1]["path"] and data["boundary"] == "after-files":
        record = application.read(engine, data["task"]["track"])
        assert record["phase"] == "applying"
        assert all(
            application.file_state(workspace / row["path"]) == row["after"]
            for row in record["intent"]["files"]
        )
        die()

def write_json(path, value):
    original_write_json(path, value)
    if Path(path) == Path(data["journal"]) and data["boundary"] == "after-intent":
        assert value["phase"] == "applying"
        die()

def command(argv, *args, **kwargs):
    if argv[:2] == ["git", "commit"] and data["boundary"] == "before-commit":
        die()
    result = original_command(argv, *args, **kwargs)
    if argv[:2] == ["git", "add"] and data["boundary"] == "after-stage":
        record = application.read(engine, data["task"]["track"])
        assert record["phase"] == "applying"
        die()
    if argv[:2] == ["git", "commit"] and data["boundary"] == "after-commit":
        die()
    return result

with file_lock(engine.store.path / "locks" / (data["task"]["track"] + ".lock")):
    with (
        patch.object(application.os, "replace", replace),
        patch.object(application, "write_json", write_json),
        patch.object(application, "command", command),
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
        "journal": str(application.journal_path(engine, task["track"])),
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


def successor(case, old_task):
    case.s.finish(old_task, {"summary": "Prior task ended after proposal interruption"})
    with case.s.transaction() as connection:
        case.s.enqueue(
            connection,
            old_task["track"],
            "work",
            "Continue after interrupted proposal",
            "proposal-recovery-successor",
        )
    task = case.s.claim("proposal-recovery-successor")
    case.assertIsNotNone(task)
    case.assertNotEqual(task["id"], old_task["id"])
    return task


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

    def test_other_task_adopts_exact_applying_after_state_instead_of_blocking(self):
        engine, old_task, workspace = self.workspace()
        changes = self.changes(workspace)
        record = interrupt(self, engine, old_task, workspace, changes, "after-stage")
        self.assertEqual(record["phase"], "applying")
        self.assertTrue(
            all(
                application.file_state(workspace / row["path"]) == row["after"]
                for row in record["intent"]["files"]
            )
        )
        new_task = successor(self, old_task)
        with patch(
            "todo_flow.engine.run_worker",
            return_value={"summary": "Successor task ran after proposal adoption"},
        ) as worker:
            engine.execute(new_task)
        worker.assert_called_once()
        recovered = application.read(engine, old_task["track"])
        self.assertEqual(recovered["phase"], "committed")
        self.assertEqual(recovered["intent"]["task"], old_task["id"])
        self.assertNotEqual(recovered["head"], record["intent"]["before_head"])
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), recovered["head"])

    def test_other_task_closes_exact_before_state_as_restored(self):
        engine, old_task, workspace = self.workspace()
        record = interrupt(
            self, engine, old_task, workspace, self.changes(workspace), "after-intent"
        )
        before_head = record["intent"]["before_head"]
        self.assertEqual(record["phase"], "applying")
        self.assertEqual(snapshot(workspace), record["intent"]["snapshot"])
        new_task = successor(self, old_task)
        with patch(
            "todo_flow.engine.run_worker", return_value={"summary": "New task after restore"}
        ) as worker:
            engine.execute(new_task)
        worker.assert_called_once()
        self.assertEqual(application.read(engine, old_task["track"])["phase"], "restored")
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), before_head)

    def test_other_task_rejects_written_but_unstaged_mixed_state(self):
        engine, old_task, workspace = self.workspace()
        record = interrupt(
            self, engine, old_task, workspace, self.changes(workspace), "after-files"
        )
        self.assertEqual(record["phase"], "applying")
        new_task = successor(self, old_task)

        with self.assertRaises(Conflict) as raised:
            engine.gate(new_task)

        self.assertIn("matches neither", str(raised.exception))
        self.assertEqual(application.read(engine, old_task["track"])["phase"], "applying")

    def test_other_task_mixed_state_conflict_has_inspect_and_resume_commands(self):
        engine, old_task, workspace = self.workspace()
        record = interrupt(
            self, engine, old_task, workspace, self.changes(workspace), "after-stage"
        )
        (workspace / "new.py").write_text("# unexpected edit\n")
        new_task = successor(self, old_task)
        journal = application.journal_path(engine, old_task["track"]).read_bytes()
        before = snapshot(workspace)
        with self.assertRaises(Conflict) as raised:
            engine.gate(new_task)
        self.assertIn("git -C", str(raised.exception))
        self.assertIn("todo-flow --state", str(raised.exception))
        self.assertEqual(snapshot(workspace), before)
        self.assertEqual(application.journal_path(engine, old_task["track"]).read_bytes(), journal)
        self.assertEqual(record["phase"], "applying")

    def test_other_task_rejects_unrelated_worktree_drift_after_proposal_write(self):
        engine, old_task, workspace = self.workspace()
        record = interrupt(
            self, engine, old_task, workspace, self.changes(workspace), "after-stage"
        )
        extra = workspace / "unrelated" / "자료 🌱.txt"
        extra.parent.mkdir()
        extra.write_text("later work\n")
        new_task = successor(self, old_task)
        journal = application.journal_path(engine, old_task["track"]).read_bytes()
        before = snapshot(workspace)

        with self.assertRaises(Conflict):
            engine.gate(new_task)

        self.assertEqual(snapshot(workspace), before)
        self.assertEqual(application.journal_path(engine, old_task["track"]).read_bytes(), journal)
        self.assertEqual(record["phase"], "applying")

    def test_recovery_rejects_25_adversarial_states_without_mutating_evidence(self):
        variants = [
            "target-bytes",
            "target-mode",
            "target-missing",
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
            "written-but-unstaged",
            "index-reset-to-before",
            "index-empty",
            "head-generation-change",
            "merge-marker-change",
            "journal-fingerprint-change",
            "journal-shape-change",
        ]
        for variant in variants:
            with self.subTest(counterexample=variant):
                case = InterruptedProposalTests()
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
                        extra = workspace / "calc.py.copy"
                        extra.write_text("unrelated later file\n")
                    elif variant == "written-but-unstaged":
                        restore_original(workspace, record)
                        for row in record["intent"]["files"]:
                            target = workspace / row["path"]
                            after = row["after"]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(base64.b64decode(after["bytes"]))
                            target.chmod(after["mode"])
                    elif variant == "index-reset-to-before":
                        restore_original(workspace, record)
                        for row in record["intent"]["files"]:
                            target = workspace / row["path"]
                            after = row["after"]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(base64.b64decode(after["bytes"]))
                            target.chmod(after["mode"])
                    elif variant == "index-empty":
                        index = Path(
                            command(["git", "rev-parse", "--git-path", "index"], workspace)
                        )
                        if not index.is_absolute():
                            index = workspace / index
                        index.write_bytes(b"invalid index")
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
                    elif variant == "journal-fingerprint-change":
                        payload = json.loads(journal_path.read_text())
                        payload["id"] = "0" * 64
                        journal_path.write_text(json.dumps(payload))
                    elif variant == "journal-shape-change":
                        payload = json.loads(journal_path.read_text())
                        payload["intent"]["files"] = {"path": "not-a-list"}
                        journal_path.write_text(json.dumps(payload))

                    new_task = successor(case, old_task)
                    before_workspace = workspace_evidence(workspace)
                    before_journal = journal_path.read_bytes()
                    with self.assertRaises(Conflict):
                        engine.gate(new_task)
                    self.assertEqual(workspace_evidence(workspace), before_workspace)
                    self.assertEqual(journal_path.read_bytes(), before_journal)
                    self.assertEqual(record["phase"], "applying")
                finally:
                    case.tearDown()

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
        with self.assertRaisesRegex(Conflict, "interrupted proposal preserved"):
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

    def test_real_precommit_interrupt_adopts_exact_written_and_staged_files(self):
        engine, task, workspace = self.workspace()
        record = interrupt(self, engine, task, workspace, self.changes(workspace), "before-commit")
        self.assertEqual(record["phase"], "ready")
        self.assertTrue((workspace / "new.py").exists())
        recovered = application.recover(engine, task, workspace)
        self.assertEqual(recovered["phase"], "committed")
        self.assertEqual(application.read(engine, task["track"])["phase"], "committed")
        self.assertNotEqual(recovered["head"], record["intent"]["before_head"])

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

    def test_precommit_merge_interruption_adopts_exact_merge_after_state(self):
        self.ready()
        task, workspace = self.repair_task()
        repair = integration.prepare(self.e, task, workspace)
        original = snapshot(workspace)
        changes = [{"path": "calc.py", "content": test_integration_repair.RESOLVED}]
        record = interrupt(self, self.e, task, workspace, changes, "before-commit", repair)
        self.assertEqual(record["phase"], "ready")
        recovered = application.recover(self.e, task, workspace)
        self.assertEqual(recovered["phase"], "committed")
        self.assertEqual(application.read(self.e, task["track"])["phase"], "committed")
        self.assertFalse(integration.merge_head(workspace))
        pending = integration.pending(self.s.track(task["track"]))
        self.assertEqual(pending["status"], "integration-repair")
        self.assertEqual(pending["resolved_tree"], record["resolved_tree"])
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [repair["candidate"], repair["base"]],
        )
        self.assertEqual((workspace / "calc.py").read_text(), test_integration_repair.RESOLVED)
        self.assertNotEqual(snapshot(workspace), original)


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
