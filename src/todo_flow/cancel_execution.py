"""Persist cancellation identity and finalize only after attributed cleanup.

Cancellation fences the task immediately. Its attempt stays running until the
execution lock and the existing launch inventory prove that all owned work has
stopped. Recovery never reconstructs a process handle from a stored PID.
"""

import json
import time

from .adapters import file_lock
from .process_barrier import ProcessBarrierError
from .process_recovery import require_recovery_clear
from .store import Conflict, encode


def record_requests(store, connection, track):
    """Record the pre-fence identity in the same transaction as cancellation."""
    tasks = connection.execute(
        "SELECT id,track,generation,owner,input_revision FROM tasks "
        "WHERE track=? AND status='running'",
        (track,),
    ).fetchall()
    for task in tasks:
        attempts = connection.execute(
            "SELECT id FROM attempts WHERE task=? AND generation=? AND status='running' "
            "ORDER BY id",
            (task["id"], task["generation"]),
        ).fetchall()
        store.event(
            connection,
            "attempt.cancel_requested",
            track,
            {"task": dict(task), "attempts": [row["id"] for row in attempts]},
        )


def recover_request(store, expected):
    """Validate the durable request again while holding the execution lock."""
    with file_lock(store.path / "locks" / (expected["track"] + ".lock")):
        with store.transaction() as connection:
            event = connection.execute(
                "SELECT * FROM events WHERE seq=?", (expected["seq"],)
            ).fetchone()
            if event is None or any(
                event[key] != expected[key] for key in ("type", "track", "body")
            ):
                raise Conflict("Cancellation request changed during recovery")
            request = json.loads(event["body"])
            task = request["task"]
            current = connection.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
            if (
                event["type"] != "attempt.cancel_requested"
                or task["track"] != event["track"]
                or current is None
                or current["track"] != task["track"]
                or current["generation"] <= task["generation"]
            ):
                raise Conflict("Cancellation no longer identifies a fenced claim")
            if current["generation"] == task["generation"] + 1 and (
                current["status"] != "cancelled"
                or current["owner"] != task["owner"]
                or current["input_revision"] != task["input_revision"]
            ):
                raise Conflict("Cancelled claim identity changed")
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE task=? AND generation=? ORDER BY id",
                (task["id"], task["generation"]),
            ).fetchall()
            if len(attempts) != 1 or [row["id"] for row in attempts] != request["attempts"]:
                raise ProcessBarrierError("Cancellation requires one matching attempt identity")
            attempt = attempts[0]
            if attempt["status"] == "cancelled":
                return False
            if attempt["status"] != "running":
                raise Conflict("Cancellation attempt has a conflicting final result")
            require_recovery_clear(
                store.path,
                task["track"],
                attempt["id"],
                task=task["id"],
                generation=task["generation"],
            )
            connection.execute(
                "UPDATE attempts SET status='cancelled',finished=? "
                "WHERE id=? AND task=? AND generation=? AND status='running'",
                (time.time(), attempt["id"], task["id"], task["generation"]),
            )
            connection.execute(
                "UPDATE tasks SET lease=NULL WHERE id=? AND generation=? AND status='cancelled'",
                (task["id"], task["generation"] + 1),
            )
            store.event(
                connection,
                "attempt.cancelled",
                task["track"],
                {
                    "attemptId": attempt["id"],
                    "workId": task["id"],
                    "generation": task["generation"],
                    "request": event["seq"],
                },
            )
        return True


def reconcile_cancelled(store, track=None):
    """Recover pending cancellations, keeping uncertain attempts diagnosable."""
    with store.connect() as connection:
        running = {
            (row["task"], row["generation"])
            for row in connection.execute(
                "SELECT task,generation FROM attempts WHERE status='running'"
            )
        }
        requests = []
        for event in connection.execute(
            "SELECT * FROM events WHERE type='attempt.cancel_requested' ORDER BY seq"
        ):
            if track is not None and event["track"] != track:
                continue
            task = json.loads(event["body"])["task"]
            if (task["id"], task["generation"]) in running:
                requests.append(dict(event))
    blocked = set()
    for event in requests:
        try:
            recover_request(store, event)
        except (Conflict, ProcessBarrierError) as error:
            blocked.add(event["track"])
            evidence = {"request": event["seq"], "reason": str(error)}
            with store.transaction() as connection:
                if not connection.execute(
                    "SELECT 1 FROM events WHERE type='attempt.cancel_blocked' "
                    "AND track=? AND body=?",
                    (event["track"], encode(evidence)),
                ).fetchone():
                    store.event(connection, "attempt.cancel_blocked", event["track"], evidence)
    return blocked
