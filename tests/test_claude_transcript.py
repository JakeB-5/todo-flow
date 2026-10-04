import json
from pathlib import Path
import tempfile
import unittest

from todo_flow.claude_transcript import ClaudeTranscript


class ClaudeTranscriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = {
            "session": "owned-session",
            "workspace": str(self.root),
            "projects": str(self.root / "projects"),
            "prompt": "Read the assigned task and return a proposal.",
        }
        self.path = self.root / "projects/project/owned-session.jsonl"
        self.path.parent.mkdir(parents=True)
        self.reader = ClaudeTranscript(self.spec)

    def row(self, kind, uuid, **values):
        return {
            "type": kind,
            "uuid": uuid,
            "sessionId": "owned-session",
            "cwd": str(self.root),
            "isSidechain": False,
            **values,
        }

    def append(self, *rows):
        with self.path.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def prompt(self):
        return self.row("user", "request", message={"content": self.spec["prompt"]})

    def response(self, **overrides):
        return self.row(
            "assistant",
            "answer",
            message={
                "id": "provider-message",
                "model": "fixture-model",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"summary":"A complete proposal"}'}],
                **overrides,
            },
        )

    def completion(self, **overrides):
        return self.row("system", "completed-turn", subtype="turn_duration", **overrides)

    def test_text_and_partial_rows_do_not_finish_until_the_owned_turn_completes(self):
        self.append(self.prompt(), self.response())
        self.assertIsNone(self.reader.poll())
        marker = json.dumps(self.completion()) + "\n"
        with self.path.open("a") as stream:
            stream.write(marker[:20])
        self.assertIsNone(self.reader.poll())
        with self.path.open("a") as stream:
            stream.write(marker[20:])
        result = self.reader.poll()
        self.assertEqual(result["proposal"], {"summary": "A complete proposal"})
        self.assertEqual(result["turn"], "request")
        self.assertEqual(result["completion"], "completed-turn")
        self.assertEqual(result["model"], "fixture-model")

    def test_forged_session_workspace_and_second_prompt_are_rejected(self):
        for change in (
            {"sessionId": "other"},
            {"cwd": "/other"},
            {"isSidechain": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ClaudeTranscript(self.spec).receive({**self.prompt(), **change})
        self.reader.receive(self.prompt())
        with self.assertRaisesRegex(ValueError, "unexpected prompt"):
            self.reader.receive(self.prompt())

    def test_only_read_tools_and_matched_results_may_precede_a_proposal(self):
        self.append(
            self.prompt(),
            self.row(
                "assistant",
                "read",
                message={"content": [{"type": "tool_use", "name": "Read", "id": "tool-one"}]},
            ),
            self.row(
                "user",
                "read-result",
                message={
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-one", "content": "source"}
                    ]
                },
            ),
            self.response(),
            self.completion(),
        )
        self.assertIsNotNone(self.reader.poll())
        reader = ClaudeTranscript(self.spec)
        reader.receive(self.prompt())
        with self.assertRaisesRegex(ValueError, "read-only contract"):
            reader.receive(
                self.row(
                    "assistant",
                    "write",
                    message={"content": [{"type": "tool_use", "name": "Bash", "id": "bad"}]},
                )
            )

    def test_incomplete_or_failed_turns_cannot_supply_a_proposal(self):
        for response, completion in (
            (self.response(stop_reason="max_tokens"), self.completion()),
            (self.response(), self.completion(pendingBackgroundAgentCount=1)),
            (self.response(content=[{"type": "text", "text": "unfinished"}]), self.completion()),
        ):
            with self.subTest(response=response):
                reader = ClaudeTranscript(self.spec)
                reader.receive(self.prompt())
                reader.receive(response)
                with self.assertRaises(ValueError):
                    reader.receive(completion)
        self.reader.receive(self.prompt())
        with self.assertRaisesRegex(RuntimeError, "API failed"):
            self.reader.receive(
                {**self.response(), "isApiErrorMessage": True, "error": "rate_limit"}
            )

    def test_replaced_truncated_and_ambiguous_transcripts_are_rejected(self):
        self.append(self.prompt())
        self.reader.poll()
        self.path.write_text("")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.reader.poll()
        other = self.root / "projects/other/owned-session.jsonl"
        other.parent.mkdir()
        other.write_text("")
        with self.assertRaisesRegex(ValueError, "multiple"):
            ClaudeTranscript(self.spec).poll()
