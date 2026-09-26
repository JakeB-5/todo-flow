"""Synthetic public-schema payloads; no server, terminal or model is started."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from todo_flow.app_server_proposal import decode_app_server_proposal
from todo_flow.native_proposal import NativeProposalBinding
from todo_flow.worker import codex_schema


class AppServerProposalTests(unittest.TestCase):
    def setUp(self):
        self.binding = NativeProposalBinding(
            attempt="attempt-one",
            task="work-one",
            generation=1,
            head="a" * 40,
            kind="work",
            host="local",
            worktree="managed-one",
            dispatch="dispatch-one",
            session="thread-one",
            turn="turn-one",
        )
        proposal = dict.fromkeys(codex_schema()["properties"])
        proposal.update(summary="제안", changes=[{"path": "a.py", "content": "값 = 1\n"}])
        self.final = {
            "id": "message-one",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": json.dumps(proposal, ensure_ascii=False),
        }
        self.item = {
            "threadId": "thread-one",
            "turnId": "turn-one",
            "completedAtMs": 123,
            "item": deepcopy(self.final),
        }
        self.turn = {
            "threadId": "thread-one",
            "turn": {
                "id": "turn-one",
                "status": "completed",
                "items": [deepcopy(self.final)],
                "itemsView": "full",
            },
        }

    def decode(self, **kwargs):
        return decode_app_server_proposal(
            self.item,
            self.turn,
            launch=self.binding,
            current=kwargs.pop("current", self.binding),
            **kwargs,
        )

    def test_round_trip_and_documented_full_default(self):
        self.turn["turn"]["items"].insert(
            0, {"id": "progress", "type": "agentMessage", "phase": "commentary", "text": "{}"}
        )
        for explicit in (True, False):
            if not explicit:
                del self.turn["turn"]["itemsView"]
            result = self.decode()
            self.assertEqual(result["summary"], "제안")
            self.assertEqual(result["changes"], [{"path": "a.py", "content": "값 = 1\n"}])

    def test_wrong_thread_turn_or_missing_timestamp_is_rejected(self):
        for record, key in (
            ("item", "threadId"),
            ("item", "turnId"),
            ("turn", "threadId"),
            ("nested", "id"),
            ("item", "completedAtMs"),
        ):
            for value in (None, "other", True):
                with self.subTest(record=record, key=key, value=value):
                    self.setUp()
                    target = self.turn["turn"] if record == "nested" else getattr(self, record)
                    target[key] = value
                    with self.assertRaises(ValueError):
                        self.decode()

    def test_unsuccessful_or_partial_turn_cannot_release_proposal(self):
        for key, values in (
            ("status", ["failed", "interrupted", "inProgress", None]),
            ("error", [{"message": "failed"}, False]),
            ("itemsView", ["summary", "notLoaded", None, "future"]),
            ("items", [None, {}, []]),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.setUp()
                    self.turn["turn"][key] = value
                    with self.assertRaises(ValueError):
                        self.decode()

    def test_unknown_phase_and_nonassistant_items_are_not_final(self):
        for key, values in (
            ("phase", [None, "commentary", "future"]),
            ("type", ["userMessage", "reasoning"]),
            ("id", ["", None]),
            ("text", [None, 1]),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.setUp()
                    self.item["item"][key] = value
                    with self.assertRaises(ValueError):
                        self.decode()
        self.setUp()
        self.turn["turn"]["items"].insert(
            0, {"id": "unknown", "type": "agentMessage", "text": "{}"}
        )
        with self.assertRaises(ValueError):
            self.decode()

    def test_conflicting_missing_or_duplicate_final_is_rejected(self):
        variants = [
            [],
            [dict(self.final, text="{}")],
            [dict(self.final, id="other")],
            [self.final, self.final],
            [self.final, dict(self.final, id="other")],
            [self.final, {"id": "message-one", "type": "reasoning"}],
        ]
        for items in variants:
            with self.subTest(items=items):
                self.turn["turn"]["items"] = items
                with self.assertRaises(ValueError):
                    self.decode()

    def test_complete_events_do_not_bypass_proposal_or_host_validation(self):
        for text in (self.final["text"][:-1], "{}", "[]"):
            self.item["item"]["text"] = text
            self.turn["turn"]["items"][0]["text"] = text
            with self.assertRaises(ValueError):
                self.decode()
        self.setUp()
        for update in ({"head": "b" * 40}, {"generation": 2}, {"session": "other"}):
            with self.subTest(update=update):
                with self.assertRaises(ValueError):
                    self.decode(current=replace(self.binding, **update))

    def test_review_requires_independent_session_provenance(self):
        self.binding = replace(self.binding, kind="review")
        for provenance in (frozenset(), frozenset({("local", "thread-one")})):
            with self.subTest(provenance=provenance):
                with self.assertRaises(ValueError):
                    self.decode(implementation_sessions=provenance)

    def test_missing_completion_records_are_rejected(self):
        for item, turn in ((None, self.turn), (self.item, None), ({}, {})):
            with self.subTest(item=item, turn=turn):
                with self.assertRaises(ValueError):
                    decode_app_server_proposal(
                        item, turn, launch=self.binding, current=self.binding
                    )
