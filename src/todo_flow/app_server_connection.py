"""In-memory protocol state for one dedicated App Server proxy connection.

No transport, launch, retry, persistence, or effect authorization is implemented.
The host must send returned messages exactly once, in order, and call disconnect
on EOF, timeout, or uncertain delivery. A new object is not recovery permission.
Only decoded messages from the owned connection may be passed to receive().

Uses the public generated InitializeParams and JSONRPC schemas captured on
2026-09-27. This validates routing fields, not every server response field.
Configuration/tool isolation and process termination remain host obligations.
"""

from copy import deepcopy
import json

from .app_server_session import (
    AppServerTurnCollector,
    thread_start_params,
    turn_start_params,
)


class AppServerConnection:
    """One initialize, fresh thread, and turn; no automatic retransmission.

    Request creation reserves its ID before the caller attempts delivery.
    Notifications can precede the turn/start response and host binding. Buffer
    only collector lifecycle methods; other notifications carry no completion
    authority. Unsupported server requests fail closed without granting approval.
    """

    def __init__(self, *, max_buffer_bytes=4_000_000):
        if type(max_buffer_bytes) is not int or max_buffer_bytes < 1:
            raise ValueError("Invalid App Server buffer limit")
        self._state = "new"
        self._next_id = 1
        self._pending = {}
        self._thread = None
        self._turn = None
        self._collector = None
        self._buffer = []
        self._buffer_bytes = 0
        self._max_buffer_bytes = max_buffer_bytes

    @property
    def state(self):
        return self._state

    @property
    def thread_id(self):
        return self._thread

    @property
    def turn_id(self):
        return self._turn

    def _require(self, state):
        if self._state != state:
            raise ValueError("Unexpected App Server connection state")

    def _request(self, method, params, state):
        request_id = self._next_id
        self._next_id += 1
        self._pending[request_id] = method
        self._state = state
        return {"id": request_id, "method": method, "params": params}

    def initialize(self):
        self._require("new")
        return self._request(
            "initialize",
            {
                "clientInfo": {"name": "todo-flow", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
            "initializing",
        )

    def initialized(self):
        """Return the handshake notification; caller sends before thread/start."""
        self._require("initialized-response")
        self._state = "ready"
        return {"method": "initialized"}

    def start_thread(self, workspace):
        self._require("ready")
        return self._request("thread/start", thread_start_params(workspace), "thread-pending")

    def start_turn(self, workspace, prompt):
        self._require("thread-ready")
        return self._request(
            "turn/start",
            turn_start_params(workspace, self._thread, prompt),
            "turn-pending",
        )

    def disconnect(self):
        """Permanently block this connection, including completed proposals.

        Pending IDs and known identities remain in memory for diagnostics only;
        the host needs durable evidence to reconcile uncertain requests.
        """
        self._state = "failed"
        self._buffer.clear()
        self._buffer_bytes = 0
        self._collector = None

    @staticmethod
    def _identity(result, key):
        value = result.get(key)
        identity = value.get("id") if isinstance(value, dict) else None
        if not isinstance(identity, str) or not identity.strip() or "\x00" in identity:
            raise ValueError("Missing App Server response identity")
        return identity

    def receive(self, message):
        """Route one decoded RPC message; malformed/foreign traffic poisons state."""
        if self._state in {"failed", "delivered", "new"}:
            raise ValueError("App Server connection is not receiving")
        try:
            if not isinstance(message, dict):
                raise ValueError("Invalid App Server message")
            if "method" in message:
                if "id" in message or "result" in message or "error" in message:
                    raise ValueError("Unsupported App Server request or mixed envelope")
                self._notification(message)
            else:
                self._response(message)
        except (ValueError, TypeError, RecursionError):
            self.disconnect()
            raise

    def _response(self, message):
        request_id = message.get("id")
        if type(request_id) is not int or request_id not in self._pending:
            raise ValueError("Unknown or duplicate App Server response ID")
        if "error" in message or "result" not in message:
            raise ValueError("App Server request failed or response is incomplete")
        result = message["result"]
        if not isinstance(result, dict):
            raise ValueError("Invalid App Server response result")
        method = self._pending[request_id]
        if method == "initialize":
            self._require("initializing")
            for key in ("codexHome", "platformFamily", "platformOs", "userAgent"):
                if not isinstance(result.get(key), str):
                    raise ValueError("Incomplete App Server initialize response")
            self._state = "initialized-response"
        elif method == "thread/start":
            self._require("thread-pending")
            self._thread = self._identity(result, "thread")
            self._state = "thread-ready"
        else:
            self._require("turn-pending")
            self._turn = self._identity(result, "turn")
            self._state = "binding-pending"
        del self._pending[request_id]

    def _notification(self, message):
        method = message["method"]
        if not isinstance(method, str):
            raise ValueError("Invalid App Server notification method")
        if method not in {"turn/started", "item/completed", "turn/completed"}:
            return
        params = message.get("params")
        if self._state == "collecting":
            self._collector.feed(method, params)
            return
        if self._state not in {"turn-pending", "binding-pending"}:
            raise ValueError("Unexpected App Server lifecycle notification")
        size = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        if self._buffer_bytes + size > self._max_buffer_bytes:
            raise ValueError("App Server early notification buffer exceeded")
        self._buffer.append((method, deepcopy(params)))
        self._buffer_bytes += size

    def bind(self, launch, *, implementation_sessions=frozenset()):
        """Bind a host record to response identities before replaying early events.

        Never substitute server identities into the caller's record implicitly.
        The host must preserve/check its attempt, claim, HEAD and session record.
        """
        self._require("binding-pending")
        try:
            collector = AppServerTurnCollector(
                launch, implementation_sessions=implementation_sessions
            )
            if launch.session != self._thread or launch.turn != self._turn:
                raise ValueError("App Server response and host binding disagree")
            for method, params in self._buffer:
                collector.feed(method, params)
        except (ValueError, TypeError, RecursionError):
            self.disconnect()
            raise
        self._collector = collector
        self._buffer.clear()
        self._buffer_bytes = 0
        self._state = "collecting"

    def proposal(self, *, current):
        self._require("collecting")
        if self._collector.state != "completed":
            raise ValueError("App Server proposal is not complete")
        try:
            result = self._collector.proposal(current=current)
        except ValueError:
            self.disconnect()
            raise
        self._state = "delivered"
        return result
