"""Synthetic decoded proxy traffic; no transport, model, or Orca calls."""

from dataclasses import replace
import json
import unittest

from todo_flow.app_server_connection import AppServerConnection
from todo_flow.native_proposal import NativeProposalBinding
from todo_flow.worker import codex_schema


class AppServerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.connection = AppServerConnection()
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
        proposal["summary"] = "합성 제안"
        item = {
            "id": "message-one",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": json.dumps(proposal),
        }
        self.events = [
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-one",
                    "turn": {"id": "turn-one", "status": "inProgress", "items": []},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-one",
                    "turnId": "turn-one",
                    "item": item,
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-one",
                    "turn": {
                        "id": "turn-one",
                        "status": "completed",
                        "items": [item],
                    },
                },
            },
        ]

    def initialize(self):
        request = self.connection.initialize()
        self.assertEqual(request["method"], "initialize")
        self.connection.receive(
            {
                "id": request["id"],
                "result": {
                    "codexHome": "/isolated",
                    "platformFamily": "unix",
                    "platformOs": "macos",
                    "userAgent": "synthetic",
                },
            }
        )
        self.assertEqual(self.connection.initialized(), {"method": "initialized"})

    def start(self):
        self.initialize()
        request = self.connection.start_thread("/workspace")
        self.connection.receive(
            {"id": request["id"], "result": {"thread": {"id": "thread-one"}}}
        )
        turn = self.connection.start_turn("/workspace", "prompt")
        self.assertNotEqual(request["id"], turn["id"])
        self.assertEqual(turn["params"]["threadId"], "thread-one")
        return turn

    def respond(self, request):
        self.connection.receive(
            {"id": request["id"], "result": {"turn": {"id": "turn-one"}}}
        )

    def test_early_and_late_notifications_deliver_identical_proposals(self):
        for split in range(4):
            with self.subTest(split=split):
                self.setUp()
                request = self.start()
                for event in self.events[:split]:
                    self.connection.receive(event)
                self.respond(request)
                self.connection.bind(self.binding)
                for event in self.events[split:]:
                    self.connection.receive(event)
                result = self.connection.proposal(current=self.binding)
                self.assertEqual(result["summary"], "합성 제안")
                with self.assertRaises(ValueError):
                    self.connection.proposal(current=self.binding)

    def test_buffer_is_snapshot_and_waits_for_host_binding(self):
        request = self.start()
        self.respond(request)
        for event in self.events:
            self.connection.receive(event)
        self.events[1]["params"]["item"]["text"] = "{}"
        with self.assertRaises(ValueError):
            self.connection.proposal(current=self.binding)
        self.connection.bind(self.binding)
        self.assertEqual(
            self.connection.proposal(current=self.binding)["summary"], "합성 제안"
        )

    def test_handshake_and_pending_start_cannot_be_reissued(self):
        with self.assertRaises(ValueError):
            self.connection.start_thread("/workspace")
        self.connection.initialize()
        for action in (
            self.connection.initialize,
            self.connection.initialized,
            lambda: self.connection.start_thread("/workspace"),
        ):
            with self.assertRaises(ValueError):
                action()
        self.setUp()
        self.start()
        with self.assertRaises(ValueError):
            self.connection.start_turn("/workspace", "duplicate")

    def test_response_ids_are_exact_and_errors_fail_closed(self):
        for response in (
            {"id": 99, "result": {}},
            {"id": "1", "result": {}},
            {"id": True, "result": {}},
            {"id": 1, "result": {}},
            {"id": 1, "error": {"code": -1, "message": "failed"}},
            {"id": 1, "result": {}, "error": {}},
        ):
            with self.subTest(response=response):
                self.setUp()
                self.connection.initialize()
                with self.assertRaises(ValueError):
                    self.connection.receive(response)
                self.assertEqual(self.connection.state, "failed")
        self.setUp()
        request = self.start()
        self.respond(request)
        with self.assertRaises(ValueError):
            self.respond(request)

    def test_wrong_binding_or_early_notification_identity_is_rejected(self):
        for field in ("session", "turn"):
            self.setUp()
            request = self.start()
            self.respond(request)
            with self.assertRaises(ValueError):
                self.connection.bind(replace(self.binding, **{field: "foreign"}))
            self.assertEqual(self.connection.state, "failed")
        for field in ("threadId", "turnId"):
            self.setUp()
            request = self.start()
            self.events[1]["params"][field] = "foreign"
            for event in self.events:
                self.connection.receive(event)
            self.respond(request)
            with self.assertRaises(ValueError):
                self.connection.bind(self.binding)
            self.assertEqual(self.connection.state, "failed")

    def test_disconnect_blocks_uncertain_start_and_completed_proposal(self):
        for complete in (False, True):
            self.setUp()
            request = self.start()
            if complete:
                self.respond(request)
                self.connection.bind(self.binding)
                for event in self.events:
                    self.connection.receive(event)
            self.connection.disconnect()
            for action in (
                lambda: self.respond(request),
                lambda: self.connection.start_turn("/workspace", "retry"),
                lambda: self.connection.proposal(current=self.binding),
            ):
                with self.assertRaises(ValueError):
                    action()

    def test_server_requests_and_buffer_overflow_block_collection(self):
        self.start()
        with self.assertRaises(ValueError):
            self.connection.receive(
                {"id": "server-one", "method": "item/commandExecution/requestApproval"}
            )
        self.assertEqual(self.connection.state, "failed")
        self.connection = AppServerConnection(max_buffer_bytes=1)
        self.start()
        with self.assertRaises(ValueError):
            self.connection.receive(self.events[0])
        self.assertEqual(self.connection.state, "failed")

    def test_stale_head_and_implementation_session_cannot_release_proposal(self):
        request = self.start()
        self.respond(request)
        self.connection.bind(self.binding)
        for event in self.events:
            self.connection.receive(event)
        with self.assertRaises(ValueError):
            self.connection.proposal(current=replace(self.binding, head="b" * 40))
        self.assertEqual(self.connection.state, "failed")
        self.setUp()
        request = self.start()
        self.respond(request)
        with self.assertRaises(ValueError):
            self.connection.bind(
                replace(self.binding, kind="review"),
                implementation_sessions=frozenset({("local", "thread-one")}),
            )
        self.assertEqual(self.connection.state, "failed")
