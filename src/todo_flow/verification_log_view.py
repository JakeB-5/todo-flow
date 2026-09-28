"""Read-only log views shared by worker handoffs and dashboard evidence."""

from urllib.parse import quote, urlencode

from . import verification_logs as logs


def _result(directory, track, verification):
    if verification is None:
        return None
    if "logReference" not in verification:
        return logs.describe(directory, None)
    ref = verification["logReference"]
    if ref is None:
        return {"format": "not-started", "complete": False, "reference": None}
    if not isinstance(ref, dict) or ref.get("track") != track:
        return {
            "format": "unavailable",
            "complete": False,
            "diagnostic": "Verification log reference belongs to another track",
        }
    return logs.describe(directory, ref)


def log_view(directory, track, verification):
    """Do not substitute the latest execution's output for a cached result."""
    result = _result(directory, track, verification)
    latest = logs.latest(directory, track)
    if (
        result
        and latest
        and result.get("reference") is not None
        and result.get("reference") == latest.get("reference")
    ):
        latest = None
    for source, summary in (("result", result), ("latest", latest)):
        if not summary or summary.get("format") != "file-backed-v1":
            continue
        for stream, value in summary["streams"].items():
            query = urlencode(
                {
                    "source": source,
                    "execution": summary["reference"]["execution"],
                    "stream": stream,
                    "offset": 0,
                    "limit": logs.TAIL_BYTES,
                }
            )
            value["readUrl"] = f"/api/tracks/{quote(track, safe='')}/evidence/verification?{query}"
    return {"result": result, "latestExecution": latest}


def read_log_range(
    directory,
    track,
    verification,
    *,
    stream,
    execution,
    source="result",
    offset=0,
    limit=logs.TAIL_BYTES,
):
    """Pin a bounded byte range to a displayed execution, never a supplied path."""
    if source not in ("result", "latest"):
        raise ValueError("Unknown verification log source")
    summary = (
        _result(directory, track, verification)
        if source == "result"
        else logs.latest(directory, track)
    )
    ref = (summary or {}).get("reference")
    if not isinstance(ref, dict) or ref.get("execution") != execution:
        raise ValueError("Verification log execution is unavailable or has changed")
    try:
        return logs.read_range(directory, ref, stream, offset, limit)
    except OSError as error:
        raise ValueError("Verification output is unavailable: " + str(error)) from error
