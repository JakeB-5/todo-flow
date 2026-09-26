"""Recover one expired claim under the execution lock and a fresh store fence.

Engine must call this outside any existing store transaction. Automatic recovery
is only sound once every spawn for an attempt participates in the launch ledger;
attributed confirmation of one execution is not an inventory of all executions.
This module never signals a process or reconstructs ownership from a stored PID.
"""

import time

from .adapters import file_lock
from .process_barrier import ProcessBarrierError
from .process_recovery import require_recovery_clear
from .store import Conflict


def recover_expired_claim(store, expected):
    """Return False for a changed claim; leave unproven claims running.

    Lock contention and missing/ambiguous evidence propagate to the caller.
    The execution lock encloses the transaction's durable commit, not just its
    UPDATE statements. A delayed live driver cannot overlap a recovered claim.
    """
    with file_lock(store.path / "locks" / (expected["track"] + ".lock")):
        with store.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM tasks WHERE id=?", (expected["id"],)
            ).fetchone()
            if (
                current is None
                or current["status"] != "running"
                or current["lease"] is None
                or current["lease"] >= time.time()
                or any(
                    current[key] != expected[key]
                    for key in ("track", "generation", "owner", "lease")
                )
            ):
                return False
            attempts = connection.execute(
                "SELECT id FROM attempts WHERE task=? AND generation=? AND status='running'",
                (current["id"], current["generation"]),
            ).fetchall()
            if len(attempts) != 1:
                raise ProcessBarrierError(
                    f"Expired claim {current['id']} generation {current['generation']} "
                    f"has {len(attempts)} running attempts; reconcile attempt identity"
                )
            attempt = attempts[0]["id"]
            require_recovery_clear(
                store.path,
                current["track"],
                attempt,
                task=current["id"],
                generation=current["generation"],
            )
            changed = connection.execute(
                "UPDATE tasks SET status='queued',generation=generation+1,owner=NULL,lease=NULL "
                "WHERE id=? AND generation=? AND status='running'",
                (current["id"], current["generation"]),
            )
            if changed.rowcount != 1:
                raise Conflict("Expired claim changed during recovery")
            connection.execute(
                "UPDATE attempts SET status='abandoned',finished=? "
                "WHERE id=? AND generation=? AND status='running'",
                (time.time(), attempt, current["generation"]),
            )
            store.event(connection, "claim.recovered", current["track"], {"workId": current["id"]})
        return True
