import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_flow
import test_integration_repair
from todo_flow import integration
from todo_flow.adapters import command
from todo_flow.checkout import snapshot
from todo_flow.engine import Engine
from todo_flow.native_proposal import NativeProposalBinding, decode_native_proposal
from todo_flow.store import Conflict
from todo_flow.worker import codex_schema, validate

OLD = "value = '안녕'"
NEW = "value = '반가워 🌿'"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "replace_worker.py"


def replacement(workspace, path="calc.py", old="raise NotImplementedError", new="return a + b"):
    return {
        "path": path,
        "format": "replace-v1",
        "base_head": command(["git", "rev-parse", "HEAD"], workspace),
        "sha256": hashlib.sha256((workspace / path).read_bytes()).hexdigest(),
        "edits": [{"old": old, "new": new}],
    }


class ChangeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.edit = {
            "path": "example.py",
            "format": "replace-v1",
            "base_head": "a" * 40,
            "sha256": "b" * 64,
            "edits": [{"old": "안녕", "new": "반가워 🌿"}],
        }
        self.binding = NativeProposalBinding(
            attempt="attempt-one",
            task="work-one",
            generation=1,
            head="a" * 40,
            kind="work",
            host="local",
            worktree="repo-one::/workspace",
            dispatch="dispatch-one",
            session="session-one",
            turn="turn-one",
        )

    def native(self, changes):
        proposal = dict.fromkeys(codex_schema()["properties"])
        proposal.update(summary="제안", changes=changes)
        return decode_native_proposal(
            json.dumps(proposal), launch=self.binding, current=self.binding
        )

    def test_both_formats_pass_worker_and_native_schema(self):
        changes = [{"path": "new.py", "content": "# 새 파일\n"}, self.edit]
        self.assertEqual(validate({"summary": "제안", "changes": changes}, "work")["changes"], changes)
        self.assertEqual(self.native(changes)["changes"], changes)

    def test_unknown_mixed_and_malformed_formats_are_rejected(self):
        invalid = [
            {**self.edit, "format": "replace-v2"},
            {**self.edit, "content": "mixed"},
            {**self.edit, "edits": []},
            {**self.edit, "edits": [{"old": "", "new": "x"}]},
            {**self.edit, "edits": [{"old": "x", "new": "\ud800"}]},
            {**self.edit, "edits": [{"old": "x", "new": 1}]},
            {**self.edit, "edits": [{"old": "x", "new": "y", "offset": 0}]},
            {**self.edit, "base_head": "HEAD"},
            {**self.edit, "sha256": "b" * 63},
            {"path": "new.py", "content": "x", "format": "full-v1"},
            {"path": "new.py", "content": "\ud800"},
        ]
        missing_format = copy.deepcopy(self.edit)
        del missing_format["format"]
        invalid.append(missing_format)
        for change in invalid:
            with self.subTest(change=repr(change)):
                with self.assertRaises(ValueError):
                    validate({"summary": "제안", "changes": [change]}, "work")
                with self.assertRaises(ValueError):
                    self.native([change])
        with self.assertRaisesRegex(ValueError, "Only a work task"):
            validate({"summary": "제안", "changes": [self.edit]}, "review")


class ProposalApplicationTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def workspace(self):
        self.s.start("addition")
        assess = self.s.claim("assessor")
        self.s.finish(
            assess,
            {"summary": "구현", "next": [{"kind": "work", "purpose": "치환 구현"}]},
        )
        task = self.s.claim("author")
        engine = Engine(self.s)
        return engine, task, engine.ensure_workspace(task)

    def test_mixed_batch_preserves_original_coordinates_and_exact_bytes(self):
        engine, task, workspace = self.workspace()
        source = "alpha beta\r\n안녕 🌿\r\n"
        engine.apply_changes(task, workspace, [{"path": "words.py", "content": source}])
        edit = replacement(workspace, "words.py", "beta", "gamma")
        edit["edits"] += [{"old": "alpha", "new": "beta"}, {"old": "안녕 ", "new": ""}]
        before = command(["git", "rev-parse", "HEAD"], workspace)
        engine.apply_changes(
            task,
            workspace,
            [edit, {"path": "created.py", "content": "# 새 파일\r\n"}],
            expected_head=before,
        )
        expected = "beta gamma\r\n🌿\r\n".encode()
        self.assertEqual((workspace / "words.py").read_bytes(), expected)
        self.assertEqual(
            subprocess.check_output(["git", "show", "HEAD:words.py"], cwd=workspace), expected
        )
        self.assertEqual((workspace / "created.py").read_bytes(), "# 새 파일\r\n".encode())
        self.assertEqual(
            set(command(["git", "diff", "--name-only", before, "HEAD"], workspace).splitlines()),
            {"words.py", "created.py"},
        )

    def test_last_item_conflicts_leave_files_index_and_head_unchanged(self):
        engine, task, workspace = self.workspace()
        valid = replacement(workspace)
        bad_edits = [
            {**valid, "sha256": "0" * 64},
            {**valid, "base_head": "0" * 40},
            {**valid, "format": "replace-v2"},
            {**valid, "edits": [{"old": "not present", "new": "x"}]},
            {**valid, "edits": [{"old": "a", "new": "x"}]},
            {
                **valid,
                "edits": [{"old": "def add", "new": "x"}, {"old": "add(a", "new": "y"}],
            },
            {"path": "bad.py", "content": "\ud800"},
            {"path": "./calc.py", "content": "x"},
            {"path": "folder//file.py", "content": "x"},
            {"path": "../escape.py", "content": "x"},
            {"path": "calc.py/child.py", "content": "x"},
            {"path": "denied.txt", "content": "x"},
        ]
        before = snapshot(workspace)
        original = (workspace / "calc.py").read_bytes()
        for bad in bad_edits:
            with self.subTest(bad=repr(bad)):
                with self.assertRaises((Conflict, ValueError)):
                    engine.apply_changes(
                        task, workspace, [{"path": "created.py", "content": "x"}, bad]
                    )
                self.assertFalse((workspace / "created.py").exists())
                self.assertFalse((workspace / "folder").exists())
                self.assertEqual(snapshot(workspace), before)
                self.assertEqual((workspace / "calc.py").read_bytes(), original)

    def test_duplicate_and_ancestor_paths_are_rejected_before_writes(self):
        engine, task, workspace = self.workspace()
        before = snapshot(workspace)
        for paths in (("new.py", "new.py"), ("new.py", "new.py/child.py")):
            for order in (paths, tuple(reversed(paths))):
                with self.subTest(paths=order):
                    with self.assertRaises(Conflict):
                        engine.apply_changes(
                            task, workspace, [{"path": path, "content": "x"} for path in order]
                        )
                    self.assertFalse((workspace / "new.py").exists())
                    self.assertEqual(snapshot(workspace), before)

    def test_symlink_and_directory_targets_are_rejected_before_writes(self):
        engine, task, workspace = self.workspace()
        (workspace / "linked.py").symlink_to(workspace / "calc.py")
        (workspace / "alias").symlink_to(workspace, target_is_directory=True)
        (workspace / "directory.py").mkdir()
        before = snapshot(workspace)
        for name in ("linked.py", "alias/calc.py", "directory.py"):
            with self.subTest(name=name):
                with self.assertRaises(Conflict):
                    engine.apply_changes(
                        task,
                        workspace,
                        [{"path": "created.py", "content": "x"}, {"path": name, "content": "x"}],
                    )
                self.assertFalse((workspace / "created.py").exists())
                self.assertEqual(snapshot(workspace), before)

    def test_matching_digest_does_not_authorize_user_changes(self):
        engine, task, workspace = self.workspace()
        (workspace / "calc.py").write_bytes(b"# User recovery\n")
        edit = replacement(workspace, old="# User recovery", new="# Replacement")
        before = snapshot(workspace)
        with self.assertRaisesRegex(Conflict, "existing changes"):
            engine.apply_changes(
                task, workspace, [{"path": "created.py", "content": "x"}, edit]
            )
        self.assertEqual(snapshot(workspace), before)
        self.assertEqual((workspace / "calc.py").read_bytes(), b"# User recovery\n")
        self.assertFalse((workspace / "created.py").exists())

    def test_repeated_overlapping_occurrences_are_ambiguous(self):
        engine, task, workspace = self.workspace()
        engine.apply_changes(task, workspace, [{"path": "words.py", "content": "aaa"}])
        before = snapshot(workspace)
        with self.assertRaisesRegex(Conflict, "exactly once"):
            engine.apply_changes(task, workspace, [replacement(workspace, "words.py", "aa", "b")])
        self.assertEqual(snapshot(workspace), before)

    def test_changed_claim_cannot_apply_edit(self):
        engine, task, workspace = self.workspace()
        edit = replacement(workspace)
        before = snapshot(workspace)
        self.s.control("addition", "cancel")
        with self.assertRaises(Conflict):
            engine.apply_changes(task, workspace, [edit])
        self.assertEqual(snapshot(workspace), before)

    def test_worker_head_movement_rejects_legacy_and_edit_proposals(self):
        for use_edit in (False, True):
            with self.subTest(use_edit=use_edit):
                case = ProposalApplicationTests()
                case.setUp()
                try:
                    engine, task, workspace = case.workspace()
                    original = (workspace / "calc.py").read_bytes()

                    def worker(config, context, *args):
                        change = replacement(workspace)
                        if not use_edit:
                            change = {"path": "calc.py", "content": "# stale proposal\n"}
                        command(["git", "commit", "--allow-empty", "-m", "Concurrent"], workspace)
                        # Even a worker that updates its edit binding cannot replace
                        # the host's pre-execution context HEAD.
                        if use_edit:
                            change["base_head"] = command(["git", "rev-parse", "HEAD"], workspace)
                        return {"summary": "제안", "changes": [change]}

                    with (
                        patch("todo_flow.engine.run_worker", side_effect=worker),
                        patch.object(engine, "verify") as verify,
                    ):
                        engine.execute(task)
                    verify.assert_not_called()
                    self.assertEqual((workspace / "calc.py").read_bytes(), original)
                    decisions = case.s.snapshot()["decisions"]
                    self.assertTrue(any("HEAD changed" in row["question"] for row in decisions))
                    self.assertEqual(command(["git", "log", "-1", "--format=%s"], workspace), "Concurrent")
                finally:
                    case.tearDown()

    def run_example(self, lines, old=OLD):
        engine, task, workspace = self.workspace()
        source = ("# unchanged filler\r\n" * lines + OLD + "\r\n").encode()
        engine.apply_changes(
            task, workspace, [{"path": "large.py", "content": source.decode()}]
        )
        before = command(["git", "rev-parse", "HEAD"], workspace)
        original_calc = (workspace / "calc.py").read_bytes()
        engine.config["worker"] = {
            "type": "command",
            "argv": [sys.executable, str(EXAMPLE), "large.py", old, NEW],
        }
        engine.config["verify"] = [
            sys.executable,
            "-c",
            "from large import value; assert value == '반가워 🌿'",
        ]
        engine.execute(task)
        output = (self.s.path / "attempts" / task["attempt"] / "output.json").read_bytes()
        change = json.loads(output)["changes"][0]
        self.assertEqual(change["format"], "replace-v1")
        self.assertNotIn("content", change)
        self.assertEqual(change["sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(change["base_head"], before)
        self.assertEqual((workspace / "calc.py").read_bytes(), original_calc)
        if old == OLD:
            expected = source.replace(OLD.encode(), NEW.encode())
            self.assertEqual((workspace / "large.py").read_bytes(), expected)
            self.assertEqual(
                subprocess.check_output(["git", "show", "HEAD:large.py"], cwd=workspace), expected
            )
            self.assertEqual(
                command(["git", "diff", "--name-only", before, "HEAD"], workspace), "large.py"
            )
            self.assertEqual(command(["git", "status", "--porcelain"], workspace), "")
            self.assertTrue(json.loads(self.s.track("addition")["verification"])["ok"])
        else:
            self.assertEqual((workspace / "large.py").read_bytes(), source)
            self.assertEqual(command(["git", "rev-parse", "HEAD"], workspace), before)
            self.assertEqual(command(["git", "status", "--porcelain"], workspace), "")
            self.assertIsNone(self.s.track("addition")["verification"])
            self.assertTrue(
                any("exactly once" in row["question"] for row in self.s.snapshot()["decisions"])
            )
        return len(output), len(source)

    def test_real_command_proposal_size_does_not_grow_with_file(self):
        measurements = []
        for lines in (100, 20000):
            case = ProposalApplicationTests()
            case.setUp()
            try:
                measurements.append(case.run_example(lines))
            finally:
                case.tearDown()
        self.assertEqual(measurements[0][0], measurements[1][0])
        self.assertGreater(measurements[1][1], measurements[0][1] * 100)
        self.assertLess(measurements[0][0], measurements[0][1])

    def test_real_command_conflict_is_not_applied(self):
        self.run_example(100, old="value = '없는 원본'")


class ReplacementRepairTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown
    ready = test_integration_repair.IntegrationRepairTests.ready
    repair_task = test_integration_repair.IntegrationRepairTests.repair_task

    def test_host_assembled_resolution_preserves_merge_protection_and_parents(self):
        self.ready()
        task, workspace = self.repair_task()
        record = integration.prepare(self.e, task, workspace)
        original = (workspace / "calc.py").read_bytes().decode()
        edit = replacement(workspace, old=original, new=original + "# still conflicted\n")
        before = snapshot(workspace)
        with self.assertRaisesRegex(Conflict, "conflict markers"):
            self.e.apply_changes(task, workspace, [edit], record)
        self.assertEqual(snapshot(workspace), before)
        edit["edits"][0]["new"] = test_integration_repair.RESOLVED
        self.e.apply_changes(task, workspace, [edit], record)
        integration.finish_repair(self.e, task, workspace, record)
        self.assertEqual(
            command(["git", "show", "-s", "--format=%P", "HEAD"], workspace).split(),
            [self.before["head"], self.base],
        )
        self.assertEqual(
            (workspace / "calc.py").read_bytes(), test_integration_repair.RESOLVED.encode()
        )
        self.assertEqual((workspace / "upstream.txt").read_text(), "Upstream addition outside the write surface\n")
