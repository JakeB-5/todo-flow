"""Read-only reconciliation on the same live owned App Server; never resubmit."""

from pathlib import Path
import time

from .app_server_proposal import _final_message
from .native_proposal import check_binding, decode_native_proposal
from .unix_websocket import UnixWebSocket
from .workspace_creation import _write_exclusive


def decode_history_turn(turn, *, binding, current, implementation_sessions):
    check_binding(binding, current, implementation_sessions)
    if (
        not isinstance(turn, dict)
        or turn.get("id") != binding.turn
        or turn.get("status") != "completed"
        or turn.get("error") is not None
        or turn.get("itemsView") != "full"
        or not isinstance(turn.get("items"), list)
    ):
        raise ValueError("Native history is not a complete matching turn")
    identities = set()
    finals = []
    for item in turn["items"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in identities
        ):
            raise ValueError("Native history item identity is ambiguous")
        identities.add(item["id"])
        if item.get("type") == "agentMessage":
            if item.get("phase") == "final_answer":
                finals.append(_final_message(item))
            elif item.get("phase") != "commentary":
                raise ValueError("Native history assistant phase is ambiguous")
    if len(finals) != 1:
        raise ValueError("Native history has no unique complete final message")
    return decode_native_proposal(
        finals[0]["text"],
        launch=binding,
        current=current,
        implementation_sessions=implementation_sessions,
    )


def recover(socket_path, *, deadline, home, folder, binding, current, implementation_sessions):
    """Caller proves live original process and unchanged socket inode first.

    Only metadata/history reads follow initialization. No thread/start,
    thread/resume or turn/start is sent. Unknown history blocks instead of
    guessing a turn identity or converting a summary into a full result.
    """
    client = UnixWebSocket(socket_path, deadline=deadline)
    sequence = 1000

    def request(method, params):
        nonlocal sequence
        sequence += 1
        message = {"id": sequence, "method": method, "params": params}
        _write_exclusive(folder / f"native-recovery-{sequence}.json", message)
        client.send(message)
        while True:
            response = client.receive()
            if not isinstance(response, dict):
                raise ValueError("Invalid recovery response")
            if "method" in response:
                if "id" in response:
                    raise ValueError("Unsupported recovery server request")
                continue
            if (
                type(response.get("id")) is not int
                or response["id"] != sequence
                or "error" in response
            ):
                raise ValueError("Native history query was not confirmed")
            return response["result"]

    try:
        initialized = request(
            "initialize",
            {
                "clientInfo": {"name": "todo-flow-recovery", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        if Path(initialized.get("codexHome", "")).resolve() != home.resolve():
            raise ValueError("Recovery connected to another native server")
        client.send({"method": "initialized"})
        thread = request("thread/read", {"threadId": binding.session, "includeTurns": False})[
            "thread"
        ]
        if thread.get("id") != binding.session:
            raise ValueError("Recovery returned another native thread")
        while time.monotonic() < deadline:
            page = request(
                "thread/turns/list", {"threadId": binding.session, "itemsView": "full", "limit": 2}
            )
            turns = page.get("data")
            if (
                page.get("nextCursor") is not None
                or not isinstance(turns, list)
                or len(turns) != 1
                or turns[0].get("id") != binding.turn
            ):
                raise ValueError("Native session history is absent or has another turn")
            turn = turns[0]
            if turn.get("status") == "inProgress":
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
                continue
            proposal = decode_history_turn(
                turn,
                binding=binding,
                current=current(),
                implementation_sessions=implementation_sessions,
            )
            _write_exclusive(folder / "native-recovered-turn.json", turn)
            return proposal
        raise TimeoutError("Native history did not complete before the deadline")
    finally:
        client.close()
