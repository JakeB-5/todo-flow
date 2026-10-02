"""Replay synthetic review responses without workers, models, or remote effects."""

import argparse
import hashlib
import json
from pathlib import Path


METRICS = ("missed_defects", "false_approvals", "rework", "execution_failures")
STATUSES = ("completed", "skipped", "failed", "missing_response")


class FixedResponseAdapter:
    """Read recorded responses only; scoring expectations never enter the adapter."""

    def __init__(self, responses):
        self.responses = responses

    def respond(self, case_id):
        return self.responses.get(case_id)


def _identifiers(value):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("식별자는 중복 없는 비어 있지 않은 문자열의 목록이어야 합니다.")
    return set(value)


def _load_source(path):
    path = Path(path)
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("synthetic") is not True
        or not isinstance(document.get("id"), str)
        or not document["id"]
    ):
        raise ValueError("입력마다 synthetic: true와 비어 있지 않은 id가 필요합니다.")
    return document, {
        "path": path.as_posix(),
        "id": document["id"],
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _score(response, defects):
    metrics = dict.fromkeys(METRICS)
    if response is None:
        return "missing_response", metrics
    if not isinstance(response, dict) or response.get("status") not in STATUSES[:3]:
        raise ValueError("응답 status는 completed, skipped 또는 failed여야 합니다.")
    status = response["status"]
    metrics["execution_failures"] = int(status == "failed")
    if status != "completed":
        return status, metrics

    found = response.get("found_defects")
    if found is not None:
        metrics["missed_defects"] = len(defects - _identifiers(found))
    approved = response.get("approved")
    if approved is not None:
        if type(approved) is not bool:
            raise ValueError("approved는 boolean 또는 null이어야 합니다.")
        metrics["false_approvals"] = int(approved and bool(defects))
    rework = response.get("rework")
    if rework is not None:
        if type(rework) is not int or rework < 0:
            raise ValueError("rework는 음수가 아닌 정수 또는 null이어야 합니다.")
        metrics["rework"] = rework
    return status, metrics


def _aggregate(rows):
    result = {}
    for metric in METRICS:
        values = [row["metrics"][metric] for row in rows if row["metrics"][metric] is not None]
        result[metric] = {
            "observed_total": sum(values) if values else None,
            "observed_cases": len(values),
            "unobserved_cases": len(rows) - len(values),
        }
    return result


def _reference(source, collection, case_id):
    token = case_id.replace("~", "~0").replace("/", "~1")
    return f"{source['path']}#/{collection}/{token}"


def replay(cases_path, responses_path, expectations_path):
    fixture, case_source = _load_source(cases_path)
    responses, response_source = _load_source(responses_path)
    expectations, expectation_source = _load_source(expectations_path)
    sources = {
        "cases": case_source,
        "responses": response_source,
        "expectations": expectation_source,
    }
    if any(document.get("fixture_id") != fixture["id"] for document in (responses, expectations)):
        raise ValueError("응답과 기대값의 fixture_id가 사례 id와 일치해야 합니다.")
    cases = fixture["cases"]
    recorded = responses["responses"]
    expected_cases = expectations["cases"]
    if not all(isinstance(value, dict) for value in (cases, recorded, expected_cases)):
        raise ValueError("cases와 responses는 식별자를 키로 사용하는 객체여야 합니다.")
    order = fixture["order"]
    ordered_ids = _identifiers(order)
    if not order or ordered_ids != set(cases) or ordered_ids != set(expected_cases):
        raise ValueError("order는 모든 사례와 기대값을 정확히 한 번씩 포함해야 합니다.")
    if set(recorded) - ordered_ids:
        raise ValueError("응답에 등록되지 않은 사례가 있습니다.")

    adapter = FixedResponseAdapter(recorded)
    rows = []
    status_counts = dict.fromkeys(STATUSES, 0)
    for case_id in order:
        case = cases[case_id]
        if not isinstance(case, dict) or not isinstance(case.get("prompt"), str):
            raise ValueError("각 사례에는 문자열 prompt가 필요합니다.")
        defects = _identifiers(expected_cases[case_id]["defects"])
        response = adapter.respond(case_id)
        status, metrics = _score(response, defects)
        status_counts[status] += 1
        rows.append(
            {
                "case_id": case_id,
                "status": status,
                "case_ref": _reference(case_source, "cases", case_id),
                "response_ref": (
                    _reference(response_source, "responses", case_id)
                    if response is not None
                    else None
                ),
                "expectation_ref": _reference(expectation_source, "cases", case_id),
                "metrics": metrics,
            }
        )
    totals = _aggregate(rows)
    return {
        "schema_version": 1,
        "synthetic": True,
        "purpose": "synthetic 집계 검증이며 실제 모델 품질 비교가 아닙니다.",
        "adapter": "fixed-response",
        "sources": sources,
        "case_order": order,
        "rows": rows,
        "metrics": totals,
        "status_counts": status_counts,
        "matches_expected": (
            totals == expectations["expected_metrics"]
            and status_counts == expectations["expected_status_counts"]
        ),
        "unmeasured": {
            "model_quality": None,
            "human_review_seconds": None,
            "tokens": None,
            "cost": None,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="고정 합성 응답으로 오프라인 집계기를 검증합니다.")
    parser.add_argument("--cases", default="examples/quality-cases.json")
    parser.add_argument("--responses", default="examples/quality-responses.json")
    parser.add_argument("--expectations", default="examples/quality-expectations.json")
    args = parser.parse_args(argv)
    try:
        report = replay(args.cases, args.responses, args.expectations)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["matches_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
