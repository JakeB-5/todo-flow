"""Explicit verifier results and conservative compatibility for boolean evidence."""

import json


RESULT_SUFFIX = " TODO_FLOW_RESULT_V1"


class VerificationOutcomeError(RuntimeError):
    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


def read_result(stdout):
    """Read an optional final stdout line, never infer a result from diagnostics.

    The suffix remains visible in a bounded log tail even when an oversized
    declaration loses its beginning. Such declarations fail closed.
    """
    lines = stdout.rstrip().splitlines()
    if not lines or not lines[-1].endswith(RESULT_SUFFIX):
        return None
    line = lines[-1]
    try:
        if len(line.encode("utf-8")) > 4096:
            raise ValueError("Result line exceeds 4096 bytes")
        result = json.loads(line[: -len(RESULT_SUFFIX)])
        if (
            not isinstance(result, dict)
            or result.get("outcome") not in ("passed", "failed", "inconclusive")
            or not isinstance(result.get("reason"), str)
            or not result["reason"].strip()
        ):
            raise ValueError("Expected outcome and a nonempty reason")
    except ValueError as error:
        raise VerificationOutcomeError(
            "inconclusive", "Invalid structured verification result: " + str(error)
        ) from error
    return result


def outcome(record):
    """Return effective meaning without upgrading legacy or contradictory evidence."""
    if not isinstance(record, dict):
        return "inconclusive"
    if record.get("cancelled") is True or record.get("complete") is False:
        return "inconclusive"
    declared = record.get("outcome")
    if declared == "failed" and record.get("ok") is False:
        return "failed"
    if "outcome" in record and declared != "passed":
        return "inconclusive"
    if record.get("ok") is not True or record.get("error"):
        return "inconclusive"
    checks = record.get("checks", [])
    if not isinstance(checks, list) or any(outcome(check) != "passed" for check in checks):
        return "inconclusive"
    return "passed"


def passed(record):
    return outcome(record) == "passed"


def followup(record):
    """Use the same routing for explicit verification and proposal verification."""
    status = outcome(record)
    detail = str(record.get("reason") or record.get("output") or "No diagnostic recorded")
    output = str(record.get("output") or "")
    if output and output != detail:
        detail += "\n" + output
    if status == "passed":
        next_task = {"kind": "review", "purpose": "Assess current goal"}
    elif status == "failed":
        next_task = {"kind": "work", "purpose": "Fix verification: " + detail}
    else:
        next_task = {
            "kind": "assess",
            "purpose": "Diagnose verification: "
            + detail
            + "\nPreserve candidate HEAD "
            + str(record.get("head", "unknown"))
            + ". Inspect the recorded stage, execution and environment evidence. "
            "Do not infer a product defect from an exit code or exception text. "
            "After an authorized environment/check recovery, request verify for the same "
            "candidate. If recovery is unavailable or unclear, ask a concrete question "
            "identifying the failed prerequisite and required decision. Do not install "
            "dependencies automatically or request speculative product changes.",
        }
    return {"summary": "Verification " + status + ": " + detail, "next": [next_task]}
