import copy
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from todo_flow.worker import SCHEMA, codex_schema, run_worker, worker_error


class WorkerAdapterTests(unittest.TestCase):
    def test_codex_schema_keeps_original_contract_and_nullable_optionals(self):
        original = copy.deepcopy(SCHEMA)
        schema = codex_schema()
        self.assertEqual(SCHEMA, original)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["summary"], {"type": "string"})
        changes = schema["properties"]["changes"]["anyOf"]
        self.assertEqual(changes[1], {"type": "null"})
        self.assertFalse(changes[0]["items"]["additionalProperties"])

    def test_codex_proposal_decoding_and_tool_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:

            def spawn(args, **kwargs):
                self.assertIn("--output-schema", args)
                self.assertIn("--ignore-user-config", args)
                self.assertIn("read-only", args)
                disabled = [args[i + 1] for i, arg in enumerate(args) if arg == "--disable"]
                self.assertTrue({"apps", "plugins", "multi_agent"} <= set(disabled))
                self.assertNotIn("shell_tool", disabled)
                self.assertNotIn("code_mode_host", disabled)
                enabled = [args[i + 1] for i, arg in enumerate(args) if arg == "--enable"]
                self.assertTrue({"shell_tool", "code_mode_host"} <= set(enabled))
                self.assertEqual(Path(kwargs["cwd"]), Path(tmp).resolve())
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
                from unittest.mock import Mock

                return Mock(poll=lambda: 0, returncode=0)

            with patch("todo_flow.worker.subprocess.Popen", side_effect=spawn):
                result = run_worker(
                    {"worker": {"type": "codex"}, "worker_launcher": "headless"},
                    {"goal": "Example", "workspace": tmp},
                    {"attempt": "test", "kind": "assess"},
                    tmp,
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
        with patch("todo_flow.worker.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "worker_protocol=2"):
                run_worker(
                    {"worker": {"type": "command", "argv": ["unused"]}}, {}, {}, ".", lambda _: None
                )
            spawn.assert_not_called()

    def test_claude_exposes_read_tools_only(self):
        with tempfile.TemporaryDirectory() as tmp:

            def spawn(args, **kwargs):
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
                from unittest.mock import Mock

                return Mock(poll=lambda: 0, returncode=0)

            with patch("todo_flow.worker.subprocess.Popen", side_effect=spawn):
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
