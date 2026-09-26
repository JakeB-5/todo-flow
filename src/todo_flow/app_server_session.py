"""Pure request builders and ordered notification collection for App Server v2.

Based on the public schemas captured on 2026-09-27. No connection or execution
route is enabled here. The host must isolate configuration/tools, initialize its
owned proxy connection, correlate RPC responses, and bind the exact thread/turn
before feeding notifications. Buffer early notifications in that transport;
never infer a turn ID from model text or replay a lost start request.

Read-only sandbox parameters alone do not isolate inherited MCP tools/plugins.
Collection is in-memory, not a recovery receipt or proof of process termination.
The fenced host still checks ownership/HEAD and the exit barrier before effects.
"""

from copy import deepcopy
from pathlib import Path

from .app_server_proposal import decode_app_server_proposal
from .native_proposal import check_binding
from .worker import codex_schema


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Invalid App Server " + label)
    value.encode("utf-8", errors="strict")
    return value


def _workspace(value):
    value = _text(value, "workspace")
    if not Path(value).is_absolute():
        raise ValueError("App Server workspace must be absolute")
    return value


def thread_start_params(workspace):
    """Build thread/start params; transport owns request IDs and persistence."""
    return {
        "cwd": _workspace(workspace),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "ephemeral": False,
    }


def turn_start_params(workspace, thread_id, prompt):
    """Build turn/start params for a fresh, host-owned thread with no active turn.

    clientUserMessageId is intentionally not treated as an idempotency key.
    The public schema does not promise deduplication of lost start responses.
    """
    return {
        "cwd": _workspace(workspace),
        "threadId": _text(thread_id, "thread ID"),
        "input": [{"type": "text", "text": _text(prompt, "prompt")}],
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        "outputSchema": codex_schema(),
    }


class AppServerTurnCollector:
    """Collect one bound turn from ordered, decoded host-connection notifications.

    feed() takes method/params, not arbitrary JSON-RPC messages. The connection
    layer must separately handle responses, server requests and connection loss.
    Unknown notification methods are ignored; known lifecycle events for another
    identity fail closed. Any lifecycle error permanently invalidates collection.
    """

    def __init__(self, launch, *, implementation_sessions=frozenset()):
        check_binding(launch, launch, implementation_sessions)
        self._launch = launch
        self._implementation_sessions = implementation_sessions
        self._state = "waiting"
        self._final = None
        self._completed = None

    @property
    def state(self):
        return self._state

    def feed(self, method, params):
        if self._state in {"failed", "delivered"}:
            raise ValueError("App Server collection is closed")
        if method not in {"turn/started", "item/completed", "turn/completed"}:
            return
        try:
            self._feed(method, params)
        except (ValueError, TypeError, RecursionError):
            self._state = "failed"
            raise

    def _feed(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("Invalid App Server notification params")
        if params.get("threadId") != self._launch.session:
            raise ValueError("App Server notification thread mismatch")
        if method == "item/completed":
            turn_id = params.get("turnId")
        else:
            turn = params.get("turn")
            if not isinstance(turn, dict):
                raise ValueError("Invalid App Server notification turn")
            turn_id = turn.get("id")
        if turn_id != self._launch.turn:
            raise ValueError("App Server notification turn mismatch")
        if method == "turn/started":
            if self._state != "waiting" or turn.get("status") != "inProgress":
                raise ValueError("Unexpected App Server turn start")
            self._state = "started"
            return
        if self._state != "started":
            raise ValueError("App Server completion without an active bound turn")
        if method == "item/completed":
            item = params.get("item")
            if not isinstance(item, dict):
                raise ValueError("Invalid App Server completed item")
            if item.get("type") == "agentMessage":
                if item.get("phase") not in {"commentary", "final_answer"}:
                    raise ValueError("Unknown App Server assistant phase")
                if item["phase"] == "final_answer":
                    if self._final is not None:
                        raise ValueError("Duplicate App Server final message")
                    self._final = deepcopy(params)
            return
        if self._final is None:
            raise ValueError("App Server turn completed without a final item")
        self._completed = deepcopy(params)
        self._state = "completed"

    def proposal(self, *, current):
        """Validate once against a fresh host snapshot; never authorize effects."""
        if self._state != "completed":
            raise ValueError("App Server proposal is not complete")
        try:
            result = decode_app_server_proposal(
                self._final,
                self._completed,
                launch=self._launch,
                current=current,
                implementation_sessions=self._implementation_sessions,
            )
        except ValueError:
            self._state = "failed"
            raise
        self._state = "delivered"
        return result
