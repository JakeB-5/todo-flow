"""Read-only delivery summaries; receipts are evidence, never cleanup commands."""

import json
import math

from .cleanup import receipt_path
from .store import fingerprint
from .triage import state_key


def object_value(value):
    result = json.loads(value) if isinstance(value, str) else value
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise ValueError("Expected an evidence object")
    return result


def summary(phase, reason=None, detail=None):
    return {
        "phase": phase,
        "reason": reason,
        "detail": detail[:350] if isinstance(detail, str) else None,
    }


def landing_matches(c, track, review, landing):
    # Same local proof as cleanup.confirmed_landing, without its finished/triage
    # preconditions: landing must also be visible before completion adoption.
    if (
        not track["head"]
        or review.get("verdict") != "met"
        or review.get("head") != track["head"]
        or review.get("documentRevision") != track["revision"]
        or landing.get("head") != track["head"]
        or not landing.get("merged")
    ):
        return False
    if landing.get("recovered") is True:
        return landing.get("baseBefore") == landing["merged"]
    verification = object_value(landing.get("verification"))
    effect = c.execute(
        "SELECT track,kind,intent,receipt FROM effects WHERE id=?",
        (landing.get("effectId"),),
    ).fetchone()
    if (
        verification.get("ok") is not True
        or verification.get("head") != landing["merged"]
        or effect is None
        or effect["track"] != track["id"]
        or effect["kind"] != "landing"
        or object_value(effect["receipt"]) != landing
    ):
        return False
    intent = object_value(effect["intent"])
    return (
        intent.get("candidate") == track["head"]
        and intent.get("merged") == landing["merged"]
        and intent.get("base") == landing.get("baseBefore")
        and intent.get("verification") == verification
    )


def delivery_summary(store, c, track_id):
    """Read only this page's track and its current local evidence."""
    try:
        return _delivery_summary(store, c, track_id)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return summary("check-needed", "evidence-invalid")


def _delivery_summary(store, c, track_id):
    track = dict(
        c.execute(
            "SELECT id,request,revision,head,review,landing,status,control FROM tracks WHERE id=?",
            (track_id,),
        ).fetchone()
    )
    finished = track["status"] == "done" and track["control"] == "finished"
    if not track["request"]:
        return (
            summary("check-needed", "request-missing")
            if track["status"] == "done" or track["control"] == "finished"
            else summary("pending")
        )
    event = c.execute(
        "SELECT type,at,substr(json_extract(body,'$.reason'),1,350) AS reason,"
        " json_extract(body,'$.head') AS head FROM events WHERE track=?"
        " AND type IN ('cleanup.requested','cleanup.deferred','cleanup.complete')"
        " AND CASE WHEN json_valid(body) THEN json_extract(body,'$.request') END=?"
        " ORDER BY seq DESC LIMIT 1",
        (track_id, track["request"]),
    ).fetchone()
    report = None
    try:
        report = object_value(receipt_path(store, track).read_text())
    except FileNotFoundError:
        pass
    # The engine can fail before writing a receipt (e.g. a live worker barrier).
    at = report.get("at") if report is not None else None
    timestamp = isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)
    if event and event["type"] == "cleanup.deferred" and (not timestamp or event["at"] > at):
        return summary("cleanup-deferred", "cleanup-deferred", event["reason"])
    if report is not None:
        delivery = fingerprint(
            [track[key] for key in ("request", "revision", "head", "review", "landing")]
        )
        if (
            report.get("track") != track_id
            or report.get("request") != track["request"]
            or report.get("head") != track["head"]
            or report.get("delivery") != delivery
        ):
            return summary("check-needed", "cleanup-stale")
        if report.get("dryRun") is not False:
            return summary("check-needed", "cleanup-dry-run")
        if report.get("status") == "deferred":
            detail = object_value(report.get("landing")).get("reason")
            for group in ("worktrees", "terminals"):
                for item in report.get(group, []):
                    if not detail and item.get("status") == "preserved":
                        detail = item.get("reason")
            return summary("cleanup-deferred", "cleanup-deferred", detail)
    review = object_value(track["review"])
    landing = object_value(track["landing"])
    if not landing:
        return (
            summary("check-needed", "landing-missing") if finished or report else summary("pending")
        )
    if not landing_matches(c, track, review, landing):
        return summary("check-needed", "landing-unconfirmed")
    key = state_key(store, c, track_id)
    cleared = c.execute(
        "SELECT 1 FROM triages WHERE track=? AND input_key=?"
        " AND CASE WHEN json_valid(body) THEN json_type(body,'$.cleared') END='true' LIMIT 1",
        (track_id, key),
    ).fetchone()
    if not cleared:
        if finished or (report and report.get("status") == "complete"):
            return summary("check-needed", "triage-unconfirmed")
        active = c.execute(
            "SELECT 1 FROM tasks WHERE track=? AND kind='triage'"
            " AND status IN ('queued','running','waiting') LIMIT 1",
            (track_id,),
        ).fetchone()
        return summary("triage" if active else "landed", "triage-pending")
    if not finished:
        return (
            summary("check-needed", "execution-not-finished")
            if report
            else summary("landed", "completion-pending")
        )
    if c.execute(
        "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting') LIMIT 1",
        (track_id,),
    ).fetchone():
        return summary("check-needed", "unfinished-work")
    if report is None:
        if event and event["type"] == "cleanup.requested" and event["head"] == track["head"]:
            return summary("cleanup", "cleanup-requested")
        return summary("check-needed", "cleanup-missing")
    if report.get("status") == "pending":
        return summary("cleanup", "cleanup-pending")
    proof = object_value(report.get("landing"))
    if (
        report.get("status") != "complete"
        or not timestamp
        or proof.get("status") != "confirmed"
        or proof.get("head") != track["head"]
        or proof.get("merged") != landing["merged"]
    ):
        return summary("check-needed", "cleanup-unconfirmed")
    for group, allowed in (
        ("worktrees", ("removed", "absent")),
        ("terminals", ("closed", "absent")),
    ):
        resources = report.get(group)
        if not isinstance(resources, list) or any(
            not isinstance(item, dict) or item.get("status") not in allowed for item in resources
        ):
            return summary("check-needed", "cleanup-unconfirmed")
    return summary("complete")
