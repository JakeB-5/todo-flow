"""Native retirement evidence gates real cleanup, including interrupted retries."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_cleanup_orca
from todo_flow import managed_workspace as managed
from todo_flow.cleanup import receipt_path, terminal_cleanup


def native_evidence(folder, repo, worktree="owned-worktree"):
    terminal = {
        "handle": "owned-handle",
        "tabId": "owned-tab",
        "ptyId": "owned-pty",
        "incarnationId": "owned-incarnation",
        "worktreeId": worktree,
        "executionHostId": "local",
        "title": "TODO fixture",
    }
    task = {"attempt": folder.name, "id": "fixture-task", "track": "addition"}
    launch = {
        "backend": "orca",
        "execution_mode": "orca-native",
        "cli": "synthetic-orca",
        "repo": str(repo),
        "worktree": worktree,
        "session": "owned-session",
        "turn": "owned-turn",
        "terminal": terminal,
    }
    records = {
        "launch.json": launch,
        "native-spec.json": {
            "folder": str(folder),
            "task": task,
            "head": "fixture-head",
            "worktree": worktree,
            "cli": launch["cli"],
        },
        "native-session.json": {
            "version": 1,
            "task": task,
            "head": "fixture-head",
            "host": "local",
            "worktree": worktree,
            "status": "complete",
            "server_exit": 0,
            "viewer_cleanup_error": None,
            "runtime_id": "owned-runtime",
            "terminal": terminal,
            "session": launch["session"],
            "turn": launch["turn"],
        },
        "native-viewer-close-intent.json": {
            "runtime_id": "owned-runtime",
            "terminal": terminal,
        },
        "native-viewer-close.json": {
            "ok": True,
            "_meta": {"runtimeId": "owned-runtime"},
            "result": {
                "close": {
                    "handle": terminal["handle"],
                    "tabId": terminal["tabId"],
                    "ptyKilled": True,
                }
            },
        },
        "native-viewer-retired.json": {
            "handle": terminal["handle"],
            "runtime_id": "owned-runtime",
            "close_receipt": str(folder / "native-viewer-close.json"),
            "complete_inventory_absent": True,
        },
    }
    write_records(folder, records)
    return records


def write_records(folder, records):
    for name, value in records.items():
        (folder / name).write_text(json.dumps(value))


def inventory(rows=()):
    return {
        "ok": True,
        "_meta": {"runtimeId": "owned-runtime"},
        "result": {
            "terminals": list(rows),
            "totalCount": len(rows),
            "truncated": False,
            "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
        },
    }


class NativeTerminalCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.folder = self.repo / "attempt-native"
        self.folder.mkdir()
        self.records = native_evidence(self.folder, self.repo)

    def test_retired_native_needs_no_legacy_exit_and_never_repeats_close(self):
        before = {path: path.read_bytes() for path in self.folder.iterdir()}
        with patch.object(managed, "_call", return_value=inventory()) as call:
            for dry_run in (True, False):
                result = terminal_cleanup(self.folder, dry_run)
                self.assertEqual(result["status"], "absent", result)
                self.assertEqual(result["execution_mode"], "orca-native")
            self.assertEqual(call.call_count, 2)
            for args in call.call_args_list:
                self.assertEqual(args.args[1], ["terminal", "list", "--limit", "100000", "--json"])
                self.assertEqual(args.args[2], str(self.repo))
        self.assertFalse((self.folder / "terminal-process.json").exists())
        self.assertEqual({path: path.read_bytes() for path in self.folder.iterdir()}, before)

    def test_retirement_receipt_also_covers_already_exited_pty(self):
        self.records["native-viewer-close.json"]["result"]["close"]["ptyKilled"] = False
        write_records(self.folder, self.records)
        with patch.object(managed, "_call", return_value=inventory()):
            result = terminal_cleanup(self.folder, False)
        self.assertEqual(result["status"], "absent", result)

    def test_missing_and_mismatched_evidence_preserves_without_cli_effects(self):
        for name in self.records:
            if name == "launch.json":
                continue
            with self.subTest(missing=name):
                path = self.folder / name
                original = path.read_bytes()
                path.unlink()
                with patch.object(managed, "_call") as call:
                    result = terminal_cleanup(self.folder, False)
                    self.assertEqual(result["status"], "preserved", result)
                    call.assert_not_called()
                path.write_bytes(original)
        changes = (
            ("native-spec.json", "folder", "another-attempt"),
            ("native-spec.json", "task", {"attempt": "another-attempt"}),
            ("native-session.json", "task", {"attempt": "another-attempt"}),
            ("native-session.json", "head", "different-head"),
            ("native-session.json", "worktree", "different-worktree"),
            ("native-session.json", "session", "different-session"),
            ("native-session.json", "turn", "different-turn"),
            ("native-session.json", "terminal", {}),
            ("native-session.json", "status", "viewer-accepted"),
            ("native-session.json", "server_exit", None),
            ("native-session.json", "server_exit", True),
            ("native-session.json", "viewer_cleanup_error", "User input changed"),
            ("native-viewer-close-intent.json", "terminal", {}),
            ("native-viewer-close.json", "_meta", {"runtimeId": "different-runtime"}),
            ("native-viewer-close.json", "result", {"close": {"handle": "different-handle"}}),
            ("native-viewer-retired.json", "complete_inventory_absent", False),
            ("native-viewer-retired.json", "runtime_id", "different-runtime"),
            ("native-viewer-retired.json", "close_receipt", "/another/receipt.json"),
        )
        for name, key, value in changes:
            with self.subTest(name=name, key=key):
                records = deepcopy(self.records)
                records[name][key] = value
                write_records(self.folder, records)
                with patch.object(managed, "_call") as call:
                    result = terminal_cleanup(self.folder, False)
                    self.assertEqual(result["status"], "preserved", result)
                    self.assertTrue(result["reason"])
                    call.assert_not_called()
                write_records(self.folder, self.records)

    def test_uncertain_inventory_and_reused_resources_are_preserved(self):
        responses = []
        changed_runtime = inventory()
        changed_runtime["_meta"]["runtimeId"] = "new-runtime"
        responses.append(changed_runtime)
        for key, value in (
            ("truncated", True),
            ("totalCount", 1),
            ("hostScope", {"hostIds": [], "omittedHostIds": ["local"]}),
            ("terminals", [None]),
        ):
            response = inventory()
            response["result"][key] = value
            responses.append(response)
        terminal = self.records["launch.json"]["terminal"]
        for key in ("handle", "tabId", "ptyId"):
            row = {**terminal, "handle": "new-handle", "tabId": "new-tab", "ptyId": "new-pty"}
            row.update({key: terminal[key], "incarnationId": "new-incarnation", "lastInputAt": 1})
            responses.append(inventory([row]))
        for response in responses:
            with self.subTest(response=response):
                with patch.object(managed, "_call", return_value=response) as call:
                    result = terminal_cleanup(self.folder, False)
                self.assertEqual(result["status"], "preserved", result)
                self.assertEqual(call.call_count, 1)
        with patch.object(managed, "_call", side_effect=OSError("Inventory unavailable")):
            result = terminal_cleanup(self.folder, False)
        self.assertEqual(result["status"], "preserved")
        self.assertIn("Inventory unavailable", result["reason"])

    def test_retry_cannot_substitute_a_different_native_target(self):
        with patch.object(managed, "_call", return_value=inventory()):
            previous = terminal_cleanup(self.folder, False)
        self.assertEqual(previous["status"], "absent")
        records = deepcopy(self.records)
        for name in ("launch.json", "native-session.json", "native-viewer-close-intent.json"):
            records[name]["terminal"]["incarnationId"] = "replacement"
        write_records(self.folder, records)
        with patch.object(managed, "_call") as call:
            result = terminal_cleanup(self.folder, False, previous=previous)
            call.assert_not_called()
        self.assertEqual(result["status"], "preserved")
        self.assertIn("target changed", result["reason"])
        records["launch.json"]["execution_mode"] = "legacy"
        write_records(self.folder, records)
        result = terminal_cleanup(self.folder, False, previous=previous)
        self.assertEqual(result["status"], "preserved")
        self.assertIn("launch identity changed", result["reason"])


class NativeWorktreeCleanupTests(unittest.TestCase):
    setUp = test_cleanup_orca.OrcaCleanupTests.setUp
    tearDown = test_cleanup_orca.OrcaCleanupTests.tearDown
    start = test_cleanup_orca.OrcaCleanupTests.start
    finish_managed = test_cleanup_orca.OrcaCleanupTests.finish_managed
    entry = test_cleanup_orca.OrcaCleanupTests.entry
    cleanup = test_cleanup_orca.OrcaCleanupTests.cleanup
    assert_removed = test_cleanup_orca.OrcaCleanupTests.assert_removed
    assert_preserved = test_cleanup_orca.OrcaCleanupTests.assert_preserved

    def call(self, cli, args, root):
        if args[:3] == ["terminal", "list", "--limit"]:
            self.native_queries += 1
            return self.native_inventory
        return test_cleanup_orca.OrcaCleanupTests.call(self, cli, args, root)

    def prepare_native(self):
        self.native_queries = 0
        self.native_inventory = inventory()
        self.finish_managed()
        attempt = next(
            row for row in self.s.snapshot()["attempts"] if row["status"] == "finished"
        )
        self.folder = self.s.path / "attempts" / attempt["id"]
        self.records = native_evidence(
            self.folder, self.repo, self.shown["result"]["worktree"]["id"]
        )

    def test_native_retirement_allows_git_and_orca_cleanup_and_retry(self):
        self.prepare_native()
        evidence = {path: path.read_bytes() for path in self.folder.glob("native-*.json")}
        planned = self.cleanup(dry_run=True)
        self.assertEqual(self.entry(planned)["status"], "would-remove", planned)
        self.assertEqual(self.removals, 0)
        self.assertFalse(receipt_path(self.s, self.track).exists())
        self.assert_removed(self.cleanup())
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)
        self.assertEqual(self.native_queries, 3)
        self.assertEqual({path: path.read_bytes() for path in evidence}, evidence)

    def test_missing_retirement_blocks_worktree_until_same_evidence_is_available(self):
        self.prepare_native()
        path = self.folder / "native-viewer-retired.json"
        value = path.read_bytes()
        path.unlink()
        self.assert_preserved(self.cleanup())
        self.assertEqual(self.removals, 0)
        self.assertEqual(self.native_queries, 0)
        path.write_bytes(value)
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)

    def test_cached_absence_does_not_authorize_removal_after_user_reuse(self):
        self.prepare_native()
        notes = self.candidate / "notes.txt"
        notes.write_text("user notes")
        first = self.cleanup()
        self.assert_preserved(first)
        native = next(row for row in first["terminals"] if row["attempt"] == self.folder.name)
        self.assertEqual(native["status"], "absent")
        notes.unlink()
        reused = {**self.records["launch.json"]["terminal"], "incarnationId": "user-reuse"}
        self.native_inventory = inventory([reused])
        second = self.cleanup()
        self.assert_preserved(second)
        native = next(row for row in second["terminals"] if row["attempt"] == self.folder.name)
        self.assertEqual(native["status"], "preserved")
        self.assertIn("user reuse", native["reason"])
        self.assertEqual(self.removals, 0)
        self.assertEqual(self.native_queries, 2)

    def test_interrupted_worktree_removal_rechecks_native_absence_without_replay(self):
        self.prepare_native()
        self.failure = "interrupted"
        with self.assertRaises(KeyboardInterrupt):
            self.cleanup()
        self.assertEqual(self.removals, 1)
        self.native_inventory["result"]["truncated"] = True
        report = self.cleanup()
        self.assertEqual(report["status"], "deferred", report)
        self.assertEqual(self.removals, 1)
        self.native_inventory = inventory()
        self.assert_removed(self.cleanup())
        self.assertEqual(self.removals, 1)
        self.assertEqual(self.native_queries, 3)
