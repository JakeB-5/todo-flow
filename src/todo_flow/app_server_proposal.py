"""Decode paired App Server completion payloads without authorizing effects.

Uses the public v2 ItemCompletedNotification and TurnCompletedNotification
schemas captured on 2026-09-27. Inputs are notification params from an
authenticated, host-owned App Server connection, never model-supplied envelopes,
terminal previews or worker-read output. The caller must establish their ordered
receipt for its submitted turn. This module does not establish that connection,
Orca visibility, tool isolation, replay safety or process exit.

NativeProposalBinding.session must be the exact App Server thread ID. The native adapter maps
this binding to the owned Orca resource and supervision evidence.
The fenced host must recheck ownership and
HEAD and enforce its process exit barrier before applying the returned proposal.
"""

from .native_proposal import check_binding, decode_native_proposal


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError("Invalid App Server " + label)
    return value


def _final_message(item):
    item = _object(item, "final item")
    if (
        item.get("type") != "agentMessage"
        or item.get("phase") != "final_answer"
        or not isinstance(item.get("id"), str)
        or not item["id"].strip()
        or not isinstance(item.get("text"), str)
    ):
        raise ValueError("App Server requires an identified final assistant message")
    return item


def decode_app_server_proposal(
    item_completed,
    turn_completed,
    *,
    launch,
    current,
    implementation_sessions=frozenset(),
):
    """Require matching full completion records, then apply the proposal schema.

    Summary payloads are completion markers only; their items never supply proposal
    text. notLoaded remains unsupported. Missing itemsView defaults to full. The
    caller must use the ordered collector, which rejects duplicate final items.
    No completion notification establishes process termination.
    """
    check_binding(launch, current, implementation_sessions)
    event = _object(item_completed, "item completion")
    completed = _object(turn_completed, "turn completion")
    turn = _object(completed.get("turn"), "turn")
    if (
        event.get("threadId") != launch.session
        or completed.get("threadId") != launch.session
        or event.get("turnId") != launch.turn
        or turn.get("id") != launch.turn
    ):
        raise ValueError("App Server completion identity does not match host binding")
    if type(event.get("completedAtMs")) is not int:
        raise ValueError("App Server item completion timestamp is missing or invalid")
    if turn.get("status") != "completed" or turn.get("error") is not None:
        raise ValueError("App Server turn did not complete successfully")
    if turn.get("itemsView", "full") not in {"full", "summary"}:
        raise ValueError("App Server turn items are incomplete")
    # Live App Server sends a display summary here. The authoritative body is
    # the complete final item already received on this ordered host connection;
    # the matching successful completion establishes the end of this exact turn.
    # The connection collector rejects a second final item before this point.
    if turn.get("itemsView") == "summary":
        final = _final_message(event.get("item"))
        return decode_native_proposal(
            final["text"],
            launch=launch,
            current=current,
            implementation_sessions=implementation_sessions,
        )
    items = turn.get("items")
    if not isinstance(items, list):
        raise ValueError("App Server turn items must be an array")
    final = _final_message(event.get("item"))
    finals = []
    identities = set()
    for item in items:
        item = _object(item, "turn item")
        identity = item.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise ValueError("App Server turn item identity is missing or duplicated")
        identities.add(identity)
        if item.get("type") == "agentMessage":
            if item.get("phase") not in ("commentary", "final_answer"):
                raise ValueError("App Server assistant message phase is unknown")
            if item["phase"] == "final_answer":
                finals.append(_final_message(item))
    if len(finals) != 1 or any(finals[0][key] != final[key] for key in ("id", "text")):
        raise ValueError("App Server final message is missing, ambiguous or inconsistent")
    return decode_native_proposal(
        final["text"],
        launch=launch,
        current=current,
        implementation_sessions=implementation_sessions,
    )
