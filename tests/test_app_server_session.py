"""Synthetic notifications only: no App Server, Orca, or model calls."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from todo_flow.app_server_session import (
    AppServerTurnCollector,
    thread_start_params,
    turn_start_params,
)
from todo_flow.native_proposal import NativeProposalBinding
from todo_flow.worker import codex_schema


class AppServerSessionTests(unittest.TestCase):
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
        proposal["summary"] = "제안"
        self.final = {
            "id": "message-one",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": json.dumps(proposal, ensure_ascii=False),
        }
        self.started = {
            "threadId": "thread-one",
            "turn": {"id": "turn-one", "status": "inProgress", "items": []},
        }
        self.item = {
            "threadId": "thread-one",
            "turnId": "turn-one",
            "completedAtMs": 123,
            "item": deepcopy(self.final),
        }
        self.completed = {
            "threadId": "thread-one",
            "turn": {
                "id": "turn-one",
                "status": "completed",
                "items": [deepcopy(self.final)],
            },
        }
        self.collector = AppServerTurnCollector(self.binding)

    def complete(self):
        self.collector.feed("turn/started", self.started)
        self.collector.feed("item/completed", self.item)
        self.collector.feed("turn/completed", self.completed)

    def test_requests_pin_policy_workspace_and_schema(self):
        thread = thread_start_params("/workspace")
        turn = turn_start_params("/workspace", "thread-one", "한국어 prompt")
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertFalse(thread["ephemeral"])
        for params in (thread, turn):
            self.assertEqual(params["approvalPolicy"], "never")
            self.assertEqual(params["cwd"], "/workspace")
        self.assertEqual(turn["threadId"], "thread-one")
        self.assertEqual(turn["input"], [{"type": "text", "text": "한국어 prompt"}])
        self.assertEqual(turn["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertEqual(turn["outputSchema"], codex_schema())
        turn["outputSchema"].clear()
        self.assertEqual(
            turn_start_params("/workspace", "thread-one", "prompt")["outputSchema"],
            codex_schema(),
        )

    def test_invalid_request_inputs_fail_before_transport(self):
        for workspace in ("relative", "", None, "/bad\x00path"):
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                thread_start_params(workspace)
        for thread, prompt in (("", "prompt"), ("thread", ""), (None, "prompt")):
            with self.subTest(thread=thread, prompt=prompt), self.assertRaises(ValueError):
                turn_start_params("/workspace", thread, prompt)

    def test_ordered_completion_is_snapshot_and_single_use(self):
        self.complete()
        self.item["item"]["text"] = "{}"
        self.completed["turn"]["items"].clear()
        self.assertEqual(self.collector.proposal(current=self.binding)["summary"], "제안")
        self.assertEqual(self.collector.state, "delivered")
        with self.assertRaises(ValueError):
            self.collector.proposal(current=self.binding)
        with self.assertRaises(ValueError):
            self.collector.feed("turn/started", self.started)

    def test_acceptance_deltas_and_partial_events_do_not_release_proposal(self):
        self.collector.feed("item/agentMessage/delta", {"delta": self.final["text"]})
        with self.assertRaises(ValueError):
            self.collector.proposal(current=self.binding)
        self.collector.feed("turn/started", self.started)
        self.collector.feed("item/completed", self.item)
        with self.assertRaises(ValueError):
            self.collector.proposal(current=self.binding)
        self.assertEqual(self.collector.state, "started")

    def test_missing_start_final_or_duplicate_events_poison_collection(self):
        sequences = [
            [("item/completed", self.item)],
            [("turn/completed", self.completed)],
            [("turn/started", self.started), ("turn/completed", self.completed)],
            [("turn/started", self.started)] * 2,
            [("turn/started", self.started)] + [("item/completed", self.item)] * 2,
        ]
        for sequence in sequences:
            with self.subTest(sequence=sequence):
                collector = AppServerTurnCollector(self.binding)
                with self.assertRaises(ValueError):
                    for method, params in sequence:
                        collector.feed(method, params)
                self.assertEqual(collector.state, "failed")
                with self.assertRaises(ValueError):
                    collector.feed("turn/started", self.started)

    def test_foreign_identity_and_post_completion_events_are_rejected(self):
        for key in ("threadId", "turnId"):
            self.setUp()
            self.collector.feed("turn/started", self.started)
            self.item[key] = "other"
            with self.assertRaises(ValueError):
                self.collector.feed("item/completed", self.item)
            self.assertEqual(self.collector.state, "failed")
        self.setUp()
        self.complete()
        with self.assertRaises(ValueError):
            self.collector.feed("turn/completed", self.completed)
        with self.assertRaises(ValueError):
            self.collector.proposal(current=self.binding)

    def test_decoder_rechecks_failed_partial_and_stale_completion(self):
        for patch in (
            {"status": "failed"},
            {"itemsView": "summary"},
            {"items": []},
        ):
            self.setUp()
            self.completed["turn"].update(patch)
            self.complete()
            with self.assertRaises(ValueError):
                self.collector.proposal(current=self.binding)
            self.assertEqual(self.collector.state, "failed")
        for patch in ({"head": "b" * 40}, {"generation": 2}):
            self.setUp()
            self.complete()
            with self.assertRaises(ValueError):
                self.collector.proposal(current=replace(self.binding, **patch))

    def test_review_needs_independent_provenance(self):
        binding = replace(self.binding, kind="review")
        for provenance in (frozenset(), frozenset({("local", "thread-one")})):
            with self.assertRaises(ValueError):
                AppServerTurnCollector(binding, implementation_sessions=provenance)
        collector = AppServerTurnCollector(
            binding, implementation_sessions=frozenset({("local", "implementation")})
        )
        self.assertEqual(collector.state, "waiting")
