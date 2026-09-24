import copy
import json
import tempfile
import unittest
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
                self.assertTrue({"shell_tool", "apps", "plugins", "multi_agent"} <= set(disabled))
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
                    {"worker": {"type": "codex"}},
                    {"goal": "Example"},
                    {"attempt": "test", "kind": "assess"},
                    tmp,
                    lambda _: None,
                )
            self.assertNotIn("changes", result)
            self.assertEqual(result["next"][0]["kind"], "work")

    def test_quota_error_from_stdout_survives_empty_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "stderr.log").write_text("")
            (folder / "output.json").write_text(json.dumps({"result": "Weekly limit reached"}))
            self.assertIn("Weekly limit reached", worker_error(folder, 1))
