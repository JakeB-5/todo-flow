import copy
import json
import os
import tempfile
import unittest
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from todo_flow.supervised_process import SupervisedProcess
from todo_flow.launchers import LauncherUnavailable
from unittest.mock import patch

from todo_flow.worker import (
    configured_codex_sandbox,
    SCHEMA,
    codex_schema,
    codex_workspace_tmpdir,
    run_worker,
    select_worker_launcher,
    validate_codex_sandbox,
    worker_error,
)
from todo_flow.release import check_config


class WorkerAdapterTests(unittest.TestCase):
    def test_codex_sandbox_uses_project_config_not_ambient_environment(self):
        with patch.dict(os.environ, {"TODO_FLOW_CODEX_SANDBOX": "workspace-write"}):
            self.assertEqual(configured_codex_sandbox({}), "read-only")
            self.assertEqual(
                configured_codex_sandbox({"codex_sandbox": "workspace-write"}),
                "workspace-write",
            )

    def test_project_codex_sandbox_config_fails_closed(self):
        for value in (None, True, "workspace-write-now", " read-only"):
            with (
                self.subTest(value=repr(value)),
                self.assertRaisesRegex(ValueError, "codex_sandbox"),
            ):
                check_config({"codex_sandbox": value})
        check_config({"codex_sandbox": "read-only"})
        check_config({"codex_sandbox": "workspace-write"})

    def test_multirepository_auto_launcher_stays_headless(self):
        context = {
            "workspace": "/tmp/core",
            "repositories": {"core": {}, "service": {}},
        }
        with patch(
            "todo_flow.worker.select_launcher", return_value={"backend": "headless"}
        ) as select:
            self.assertEqual(
                select_worker_launcher({"worker_launcher": "auto"}, context),
                {"backend": "headless"},
            )
        select.assert_called_once_with({"worker_launcher": "headless"}, "/tmp/core")

    def test_multirepository_explicit_orca_launcher_fails_before_routing(self):
        context = {
            "workspace": "/tmp/core",
            "repositories": {"core": {}, "service": {}},
        }
        with patch("todo_flow.worker.select_launcher") as select:
            with self.assertRaisesRegex(LauncherUnavailable, "multi-repository worker"):
                select_worker_launcher({"worker_launcher": "orca"}, context)
        select.assert_not_called()

    def run_codex(self, root, environment, *, codex_sandbox=None):
        root = Path(root)
        workspace = root / "worktree"
        workspace.mkdir()
        state = root / "state"
        observed = {}

        def spawn(args, **kwargs):
            observed["args"] = args
            observed["tmpdir"] = kwargs["env"].get("TMPDIR")
            observed["prompt"] = kwargs["stdin"].read()
            Path(args[args.index("--output-last-message") + 1]).write_text(
                json.dumps({"summary": "Inspected and verified"})
            )
            return SupervisedProcess([sys.executable, "-c", "pass"], **kwargs)

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("todo_flow.worker.SupervisedProcess", spawn),
        ):
            result = run_worker(
                {
                    "worker": {"type": "codex"},
                    "worker_launcher": "headless",
                    **({"codex_sandbox": codex_sandbox} if codex_sandbox is not None else {}),
                },
                {"workspace": str(workspace)},
                {"attempt": "sandbox-test", "kind": "assess"},
                state,
                lambda _: None,
            )
        return result, workspace, observed

    def test_codex_sandbox_defaults_to_read_only_and_keeps_inherited_tmpdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = os.environ.copy()
            environment.pop("TODO_FLOW_CODEX_SANDBOX", None)
            environment["TMPDIR"] = "/host/temp"
            result, workspace, observed = self.run_codex(tmp, environment)

            self.assertEqual(result["summary"], "Inspected and verified")
            args = observed["args"]
            self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
            self.assertEqual(observed["tmpdir"], "/host/temp")
            self.assertFalse((workspace / ".tmp").exists())

    def test_workspace_write_sandbox_uses_attempt_tmpdir_and_allows_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = os.environ.copy()
            environment["TODO_FLOW_CODEX_SANDBOX"] = "workspace-write"
            environment["TMPDIR"] = "/host/temp"
            result, workspace, observed = self.run_codex(
                tmp, environment, codex_sandbox="workspace-write"
            )

            self.assertEqual(result["summary"], "Inspected and verified")
            args = observed["args"]
            self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
            tmpdir = Path(observed["tmpdir"])
            self.assertEqual(tmpdir, (Path(tmp) / "state/attempts/sandbox-test/tmp").resolve())
            self.assertNotEqual(tmpdir, workspace / ".tmp")
            self.assertTrue(tmpdir.is_dir())
            self.assertFalse((workspace / ".tmp").exists())
            self.assertIn("Workspace-write permits project tests", observed["prompt"])

    def test_codex_sandbox_rejects_more_than_twenty_invalid_values(self):
        invalid = [
            "",
            " ",
            "read-only ",
            " read-only",
            "READ-ONLY",
            "Workspace-Write",
            "workspace_write",
            "workspace write",
            "workspace",
            "readonly",
            "read-only,workspace-write",
            "--sandbox=workspace-write",
            "/workspace-write",
            "workspace-write\n",
            "\nworkspace-write",
            "workspаce-write",
            "workspace\u2010write",
            "workspace-write\u200b",
            None,
            True,
            False,
            0,
            1,
            3.14,
            [],
            {},
            (),
            set(),
            b"read-only",
            bytearray(b"read-only"),
            object(),
        ]
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                validate_codex_sandbox(value)
        self.assertEqual(validate_codex_sandbox("read-only"), "read-only")
        self.assertEqual(validate_codex_sandbox("workspace-write"), "workspace-write")

    def test_codex_attempt_tmpdir_rejects_symlink_and_non_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            workspace = root / "worktree"
            workspace.mkdir()
            state = root / "state"
            symlink_tmpdir = state / "attempts/attempt-link/tmp"
            symlink_tmpdir.parent.mkdir(parents=True)
            symlink_tmpdir.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "private real directory"):
                codex_workspace_tmpdir(workspace, state, "attempt-link")

            file_tmpdir = state / "attempts/attempt-file/tmp"
            file_tmpdir.parent.mkdir(parents=True)
            file_tmpdir.write_text("not a directory")
            with self.assertRaisesRegex(ValueError, "private real directory"):
                codex_workspace_tmpdir(workspace, state, "attempt-file")

    def test_codex_attempt_tmpdir_is_exact_unicode_worktree_path_and_race_safe(self):
        with tempfile.TemporaryDirectory(prefix="worker temp 한글 ") as tmp:
            root = Path(tmp)
            workspace = root / "nested" / "Repo Space"
            workspace.mkdir(parents=True)
            state = root / "state"
            with ThreadPoolExecutor(max_workers=12) as pool:
                paths = list(
                    pool.map(
                        lambda _: codex_workspace_tmpdir(workspace, state, "attempt-unicode"),
                        range(24),
                    )
                )
            expected = (state / "attempts/attempt-unicode/tmp").resolve()
            self.assertEqual(set(paths), {expected})
            self.assertTrue(expected.is_dir())

            next_generation = Path(tmp) / "nested" / "Repo Space generation 2"
            next_generation.mkdir()
            next_path = codex_workspace_tmpdir(next_generation, state, "attempt-generation-2")
            self.assertEqual(next_path, (state / "attempts/attempt-generation-2/tmp").resolve())
            self.assertNotEqual(next_path, expected)

    def test_codex_attempt_tmpdir_propagates_creation_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "worktree"
            workspace.mkdir()
            state = Path(tmp) / "state"
            original_mkdir = Path.mkdir

            def deny_tmpdir(path, *args, **kwargs):
                if path.name == "tmp":
                    raise PermissionError("denied")
                return original_mkdir(path, *args, **kwargs)

            with patch.object(Path, "mkdir", deny_tmpdir):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    codex_workspace_tmpdir(workspace, state, "attempt-permission")

    def test_codex_sandbox_rejects_invalid_value_before_launcher_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "worktree"
            workspace.mkdir()
            for value in ("", "workspace-write-now", " read-only", "workspace-write\n"):
                with self.subTest(value=value):
                    environment = os.environ.copy()
                    environment["TODO_FLOW_CODEX_SANDBOX"] = value
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch(
                            "todo_flow.worker.select_launcher",
                            side_effect=AssertionError("invalid sandbox reached launcher"),
                        ) as select,
                    ):
                        with self.assertRaisesRegex(ValueError, "codex_sandbox"):
                            run_worker(
                                {
                                    "worker": {"type": "codex"},
                                    "worker_launcher": "headless",
                                    "codex_sandbox": value,
                                },
                                {"workspace": str(workspace)},
                                {"attempt": "invalid-sandbox", "kind": "assess"},
                                Path(tmp) / "state",
                                lambda _: None,
                            )
                    select.assert_not_called()

    def test_codex_schema_keeps_original_contract_and_nullable_optionals(self):
        original = copy.deepcopy(SCHEMA)
        schema = codex_schema()
        self.assertEqual(SCHEMA, original)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["summary"], {"type": "string"})
        changes = schema["properties"]["changes"]["anyOf"]
        self.assertEqual(changes[1], {"type": "null"})
        variants = changes[0]["items"]["anyOf"]
        self.assertEqual(len(variants), 2)
        for variant in variants:
            self.assertFalse(variant["additionalProperties"])
            self.assertEqual(set(variant["required"]), set(variant["properties"]))
        edits = variants[1]["properties"]["edits"]["items"]
        self.assertFalse(edits["additionalProperties"])
        self.assertEqual(set(edits["required"]), {"old", "new"})

    def test_codex_proposal_decoding_and_tool_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            state = Path(tmp) / "state"
            workspace.mkdir()

            def spawn(args, **kwargs):
                self.assertEqual(args[0], "codex")
                self.assertIn("identity", kwargs)
                self.assertIn("--output-schema", args)
                self.assertIn("--ignore-user-config", args)
                self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
                self.assertNotIn("workspace-write", args)
                self.assertEqual(kwargs["env"]["TODO_FLOW_CODEX_SANDBOX"], "read-only")
                self.assertIn('approval_policy="never"', args)
                disabled = [args[i + 1] for i, arg in enumerate(args) if arg == "--disable"]
                self.assertTrue({"apps", "plugins", "multi_agent"} <= set(disabled))
                self.assertNotIn("shell_tool", disabled)
                self.assertNotIn("code_mode_host", disabled)
                enabled = [args[i + 1] for i, arg in enumerate(args) if arg == "--enable"]
                self.assertTrue({"shell_tool", "code_mode_host"} <= set(enabled))
                self.assertEqual(Path(kwargs["cwd"]), workspace.resolve())
                self.assertIn("TASK CONTEXT", kwargs["stdin"].read())
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    json.dumps(
                        {
                            "summary": "Investigated",
                            "changes": None,
                            "question": None,
                            "next": [{"kind": "work", "purpose": "Implement requirement"}],
                        }
                    )
                )
                return SupervisedProcess([sys.executable, "-c", "pass"], **kwargs)

            # Replace only the adapter's module reference so cleanup still uses
            # real subprocesses for process inspection and a real child to reap.
            # readonly-codex-sandbox: an inherited override must not grant writes.
            with (
                patch.dict(os.environ, {"TODO_FLOW_CODEX_SANDBOX": "workspace-write"}),
                patch("todo_flow.worker.SupervisedProcess", spawn),
            ):
                result = run_worker(
                    {"worker": {"type": "codex"}, "worker_launcher": "headless"},
                    {"goal": "Example", "workspace": str(workspace)},
                    {"attempt": "test", "kind": "assess"},
                    state,
                    lambda _: None,
                )
            self.assertNotIn("changes", result)
            self.assertEqual(result["next"][0]["kind"], "work")

    def test_worker_reads_large_source_from_workspace_without_source_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "repo"
            workspace.mkdir()
            source = "# Context need not fit in stdin\n" * 10000 + "answer = 739182\n"
            (workspace / "large.py").write_text(source)
            adapter = root / "adapter.py"
            adapter.write_text(
                "import json,sys\nfrom pathlib import Path\n"
                "ctx=json.load(sys.stdin)\n"
                "assert 'answer = 739182' not in json.dumps(ctx)\n"
                "assert Path.cwd() == Path(ctx['workspace'])\n"
                "doc=json.loads(Path(ctx['paths']['document']).read_text())\n"
                "assert doc['goal'] == 'Read the answer'\n"
                "assert Path(ctx['paths']['diff']).read_text().endswith('PATCH_END')\n"
                "source=Path('large.py').read_text()\n"
                "print(json.dumps({'summary':source.splitlines()[-1]}))\n"
            )
            result = run_worker(
                {
                    "worker_protocol": 2,
                    "worker_launcher": "headless",
                    "worker": {"type": "command", "argv": [sys.executable, str(adapter)]},
                },
                {
                    "workspace": str(workspace),
                    "document": {"goal": "Read the answer"},
                    "diff": "x" * 150001 + "PATCH_END",
                    "recent_results": [{"changes": [{"content": source}]}],
                },
                {"attempt": "large", "kind": "work"},
                root / "state",
                lambda _: None,
            )
            self.assertEqual(result["summary"], "answer = 739182")
            payload = (root / "state/attempts/large/input.json").read_text()
            self.assertLess(len(payload), 2000)
            self.assertNotIn("739182", payload)
            self.assertEqual((workspace / "large.py").read_text(), source)

    def test_legacy_custom_worker_fails_before_spawn(self):
        with patch("todo_flow.worker.SupervisedProcess") as spawn:
            with self.assertRaisesRegex(ValueError, "worker_protocol=2"):
                run_worker(
                    {"worker": {"type": "command", "argv": ["unused"]}}, {}, {}, ".", lambda _: None
                )
            spawn.assert_not_called()

    def test_claude_exposes_read_tools_only(self):
        with tempfile.TemporaryDirectory() as tmp:

            def spawn(args, **kwargs):
                self.assertEqual(args[0], "claude")
                self.assertIn("identity", kwargs)
                self.assertIn("--restricted", args)
                self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
                self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
                self.assertEqual(Path(kwargs["cwd"]), Path(tmp).resolve())
                kwargs["stdout"].write(
                    json.dumps({"type": "assistant", "message": "Reading"}) + "\n"
                )
                kwargs["stdout"].write(
                    json.dumps(
                        {"type": "result", "structured_output": {"summary": "Read the files"}}
                    )
                    + "\n"
                )
                kwargs["stdout"].flush()
                return SupervisedProcess([sys.executable, "-c", "pass"], **kwargs)

            # Keep process inspection outside the adapter launch replacement.
            with patch("todo_flow.worker.SupervisedProcess", spawn):
                result = run_worker(
                    {"worker": {"type": "claude"}, "worker_launcher": "headless"},
                    {"workspace": tmp},
                    {"attempt": "read", "kind": "assess"},
                    tmp,
                    lambda _: None,
                )
            self.assertEqual(result["summary"], "Read the files")

    def test_quota_error_from_stdout_survives_empty_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "stderr.log").write_text("")
            (folder / "output.json").write_text(json.dumps({"result": "Weekly limit reached"}))
            self.assertIn("Weekly limit reached", worker_error(folder, 1))
