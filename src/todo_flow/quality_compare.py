"""Compare recorded quality replays only within comparable cohorts, without effects."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from todo_flow import quality_eval


METRICS = quality_eval.METRICS
STATUSES = quality_eval.STATUSES
INCONCLUSIVE = ("skipped", "missing_response")
FIELDS = ("model", "effort")
INPUTS = ("cases", "responses", "expectations")
KEY = ("fixture_id", "cases_sha256", "expectations_sha256", "verification")
UNMEASURED = ("model_quality", "human_review_seconds", "tokens", "cost")
PURPOSE = (
    "기록된 실행 근거를 같은 사례·기대값·검증 조건 그룹 안에서만 집계합니다. "
    "모델 순위나 성능 점수가 아닙니다."
)
DENOMINATOR = (
    "사례는 집계한 사례 수입니다. completed와 failed는 실행 상태이고, "
    "inconclusive는 skipped와 missing_response의 합입니다. "
    "completed는 실행 상태일 뿐 모델 판단이 맞았다는 근거가 아닙니다."
)
SINGLE = "같은 비교 키의 다른 기록이 없습니다."
DESCRIPTION = "명시한 실행 기록을 비교 가능한 그룹에서만 집계합니다."
RECORD_HEADER = (
    "attempt",
    "synthetic",
    "비교 키",
    "model",
    "effort",
    "attempt 시간(초)",
    "재시도",
)
WORKER_HEADER = (
    "model",
    "effort",
    "표본",
    "사례",
    "completed",
    "failed",
    "inconclusive",
    *METRICS,
    "재시도",
    "attempt 시간 합계(초)",
    "벽시계 합집합(초)",
    "attempts",
)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _pointer(path, *parts):
    tokens = "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)
    return f"{path}#/{tokens}"


def _observed(values, count):
    return {
        "observed_total": sum(values) if values else None,
        "observed_cases": len(values),
        "unobserved_cases": count - len(values),
    }


def _merge(items):
    totals = [item["observed_total"] for item in items if item["observed_total"] is not None]
    return {
        "observed_total": sum(totals) if totals else None,
        "observed_cases": sum(item["observed_cases"] for item in items),
        "unobserved_cases": sum(item["unobserved_cases"] for item in items),
    }


def _denominator(counts):
    return {
        "cases": sum(counts[status] for status in STATUSES),
        "completed": counts["completed"],
        "failed": counts["failed"],
        "inconclusive": sum(counts[status] for status in INCONCLUSIVE),
    }


def _seconds(value):
    return int(value) if float(value).is_integer() else value


def _field(worker, location, section, key):
    confirmed = section == "provider_confirmed"
    values = worker.get(section)
    if values is not None and not isinstance(values, dict):
        raise ValueError(f"worker.{section}는 객체여야 합니다.")
    if not values or key not in values:
        status = "unconfirmed" if confirmed else "missing"
        return {"value": None, "status": status, "source": None}
    value = values[key]
    if value is None:
        status = "unconfirmed" if confirmed else "delegated"
    elif _text(value):
        status = "confirmed" if confirmed else "selected"
    else:
        raise ValueError(f"worker.{section}.{key} 값이 올바르지 않습니다.")
    source = _pointer(location, "worker", section, key)
    return {"value": value, "status": status, "source": source}


def _basis(field):
    if field["confirmed"]["status"] == "confirmed":
        return {"value": field["confirmed"]["value"], "basis": "confirmed"}
    return {"value": field["selected"]["value"], "basis": field["selected"]["status"]}


def _instant(record, key):
    value = record.get(key)
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"{key}는 시간대가 있는 ISO 8601 시각이어야 합니다.")
    return moment


def _record(path):
    path = Path(path)
    raw = path.read_bytes()
    record = json.loads(raw.decode("utf-8"))
    names = ("id", "attempt", "fixture_id", "verification")
    if not isinstance(record, dict) or not all(_text(record.get(name)) for name in names):
        raise ValueError("실행 기록에 id, attempt, fixture_id, verification이 필요합니다.")
    if type(record.get("synthetic")) is not bool:
        raise ValueError("synthetic은 boolean이어야 합니다.")
    digest = hashlib.sha256(raw).hexdigest()
    source = {"path": path.as_posix(), "id": record["id"], "sha256": digest}
    location = source["path"]
    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or not all(_text(inputs.get(name)) for name in INPUTS):
        raise ValueError("inputs에 cases, responses, expectations 경로가 필요합니다.")
    replay = quality_eval.replay(*(path.parent / inputs[name] for name in INPUTS))
    sources = replay["sources"]
    expectations = sources["expectations"]
    declared = record.get("expectations")
    if not isinstance(declared, dict) or declared.get("id") != expectations["id"]:
        raise ValueError("expectations.id가 기대값 파일 id와 일치해야 합니다.")
    if declared.get("sha256") not in (None, expectations["sha256"]):
        raise ValueError("expectations.sha256이 기대값 파일 바이트와 일치해야 합니다.")
    if record["fixture_id"] != sources["cases"]["id"]:
        raise ValueError("fixture_id가 사례 파일 id와 일치해야 합니다.")
    worker = record.get("worker")
    worker = {} if worker is None else worker
    if not isinstance(worker, dict):
        raise ValueError("worker는 객체여야 합니다.")
    order = replay["case_order"]
    retries = record.get("retries")
    retries = {} if retries is None else retries
    if not isinstance(retries, dict) or set(retries) - set(order):
        raise ValueError("retries는 등록된 사례 id를 키로 사용해야 합니다.")
    observed = []
    for case_id in order:
        value = retries.get(case_id)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ValueError("재시도 수는 음수가 아닌 정수 또는 null이어야 합니다.")
        observed.append(value)
    started = _instant(record, "started_at")
    finished = _instant(record, "finished_at")
    interval = None
    if started is not None and finished is not None:
        if finished < started:
            raise ValueError("finished_at은 started_at보다 빠를 수 없습니다.")
        interval = (started, finished)
    fields = {}
    for key in FIELDS:
        fields[key] = {
            "selected": _field(worker, location, "selected", key),
            "confirmed": _field(worker, location, "provider_confirmed", key),
        }
    duration = {
        "seconds": _seconds((finished - started).total_seconds()) if interval else None,
        "status": "observed" if interval else "unobserved",
        "started_ref": _pointer(location, "started_at") if started is not None else None,
        "finished_ref": _pointer(location, "finished_at") if finished is not None else None,
    }
    comparison = {
        "fixture_id": record["fixture_id"],
        "cases_sha256": sources["cases"]["sha256"],
        "expectations_id": expectations["id"],
        "expectations_sha256": expectations["sha256"],
        "verification": record["verification"],
    }
    row = {
        "record": source,
        "attempt": record["attempt"],
        "attempt_ref": _pointer(location, "attempt"),
        "synthetic": replay["synthetic"] or record["synthetic"],
        "declared_synthetic": record["synthetic"],
        "key": comparison,
        "sources": sources,
        "worker": fields,
        "status_counts": replay["status_counts"],
        "metrics": replay["metrics"],
        "retries": _observed(observed, len(order)),
        "retries_ref": _pointer(location, "retries") if record.get("retries") is not None else None,
        "duration": duration,
    }
    return row, interval


def _attempt(row):
    return {
        "attempt": row["attempt"],
        "record": row["record"]["path"],
        "ref": row["attempt_ref"],
    }


def _union(intervals):
    if not intervals:
        return None
    total = 0.0
    start, end = None, None
    for begin, finish in sorted(intervals):
        if end is not None and begin <= end:
            end = max(end, finish)
            continue
        if end is not None:
            total += (end - start).total_seconds()
        start, end = begin, finish
    total += (end - start).total_seconds()
    return _seconds(total)


def _worker_row(basis, group):
    rows = [row for row, _ in group]
    seconds = [row["duration"]["seconds"] for row in rows]
    seconds = [value for value in seconds if value is not None]
    intervals = [interval for _, interval in group if interval is not None]
    counts = {}
    for status in STATUSES:
        counts[status] = sum(row["status_counts"][status] for row in rows)
    metrics = {}
    for metric in METRICS:
        metrics[metric] = _merge([row["metrics"][metric] for row in rows])
    return {
        "model": basis["model"],
        "effort": basis["effort"],
        "samples": len(rows),
        "attempts": [_attempt(row) for row in rows],
        "status_counts": counts,
        "denominator": _denominator(counts),
        "metrics": metrics,
        "retries": _merge([row["retries"] for row in rows]),
        "duration": {
            "attempt_seconds_total": _seconds(sum(seconds)) if seconds else None,
            "observed_attempts": len(seconds),
            "unobserved_attempts": len(rows) - len(seconds),
            "wall_clock_seconds": _union(intervals),
        },
    }


def _workers(members):
    groups = {}
    for row, interval in members:
        basis = {key: _basis(row["worker"][key]) for key in FIELDS}
        token = tuple((basis[key]["basis"], basis[key]["value"] or "") for key in FIELDS)
        groups.setdefault(token, (basis, []))[1].append((row, interval))
    return [_worker_row(*groups[token]) for token in sorted(groups)]


def compare(record_paths):
    """Read only the listed records and the local inputs they reference."""
    loaded = [_record(path) for path in record_paths]
    if not loaded:
        raise ValueError("비교할 실행 기록을 하나 이상 지정해야 합니다.")
    rows = [row for row, _ in loaded]
    paths = {row["record"]["path"] for row in rows}
    attempts = {row["attempt"] for row in rows}
    if len(paths) != len(rows) or len(attempts) != len(rows):
        raise ValueError("같은 실행 기록이나 attempt를 두 번 집계할 수 없습니다.")
    groups = {}
    for row, interval in loaded:
        token = tuple(row["key"][name] for name in KEY)
        groups.setdefault(token, []).append((row, interval))
    cohorts, non_comparable = [], []
    for token in sorted(groups):
        members = groups[token]
        key = members[0][0]["key"]
        if len(members) == 1:
            refs = [_attempt(members[0][0])]
            non_comparable.append({"key": key, "reason": SINGLE, "attempts": refs})
            continue
        workers = _workers(members)
        counts = {}
        for status in STATUSES:
            counts[status] = sum(worker["status_counts"][status] for worker in workers)
        cohort = {"key": key, "samples": len(members), "denominator": _denominator(counts)}
        cohort["workers"] = workers
        cohorts.append(cohort)
    return {
        "schema_version": 1,
        "synthetic": any(row["synthetic"] for row in rows),
        "purpose": PURPOSE,
        "comparison_key": list(KEY),
        "denominator_definition": DENOMINATOR,
        "records": rows,
        "cohorts": cohorts,
        "non_comparable": non_comparable,
        "unmeasured": dict.fromkeys(UNMEASURED),
    }


def _escape(value):
    return str(value).replace("|", "\\|")


def _line(cells):
    return "| " + " | ".join(cells) + " |"


def _table(header, rows):
    lines = [_line(header), _line("---" for _ in header)]
    lines.extend(_line(_escape(cell) for cell in row) for row in rows)
    return lines


def _value(value):
    return "미관측" if value is None else str(value)


def _count(item):
    total = _value(item["observed_total"])
    cases, missing = item["observed_cases"], item["unobserved_cases"]
    return f"{total} (관측 {cases}, 미관측 {missing})"


def _inconclusive(counts):
    total = sum(counts[status] for status in INCONCLUSIVE)
    skipped, missing = counts["skipped"], counts["missing_response"]
    return f"{total} (skipped {skipped}, missing_response {missing})"


def _totals(denominator):
    cases, completed = denominator["cases"], denominator["completed"]
    failed, inconclusive = denominator["failed"], denominator["inconclusive"]
    return f"사례 {cases}, completed {completed}, failed {failed}, inconclusive {inconclusive}"


def _label(value, status):
    shown = "-" if value is None else value
    return f"{shown} ({status})"


def _claim(field):
    selected = _label(field["selected"]["value"], field["selected"]["status"])
    confirmed = _label(field["confirmed"]["value"], field["confirmed"]["status"])
    return f"선택 {selected} / 확인 {confirmed}"


def _key(key):
    fixture = key["fixture_id"]
    expected = key["expectations_id"]
    verification = key["verification"]
    return f"fixture `{fixture}`, 기대값 `{expected}`, 검증 `{verification}`"


def _ref(item):
    attempt, ref = item["attempt"], item["ref"]
    return f"{attempt} ({ref})"


def _refs(attempts):
    return ", ".join(_ref(item) for item in attempts)


def _record_cells(row):
    return (
        _ref(_attempt(row)),
        json.dumps(row["synthetic"]),
        _key(row["key"]),
        _claim(row["worker"]["model"]),
        _claim(row["worker"]["effort"]),
        _value(row["duration"]["seconds"]),
        _count(row["retries"]),
    )


def _worker_cells(worker):
    duration = worker["duration"]
    denominator = worker["denominator"]
    observed = duration["observed_attempts"]
    missing = duration["unobserved_attempts"]
    total = _value(duration["attempt_seconds_total"])
    return (
        _label(worker["model"]["value"], worker["model"]["basis"]),
        _label(worker["effort"]["value"], worker["effort"]["basis"]),
        worker["samples"],
        denominator["cases"],
        denominator["completed"],
        denominator["failed"],
        _inconclusive(worker["status_counts"]),
        *(_count(worker["metrics"][metric]) for metric in METRICS),
        _count(worker["retries"]),
        f"{total} (관측 {observed}, 미관측 {missing})",
        _value(duration["wall_clock_seconds"]),
        _refs(worker["attempts"]),
    )


def render_markdown(report):
    synthetic = json.dumps(report["synthetic"])
    purpose = report["purpose"]
    definition = report["denominator_definition"]
    lines = [
        "# 기록된 품질·시간·재시도 비교",
        "",
        f"- synthetic: `{synthetic}`",
        f"- {purpose}",
        "- tokens·cost는 미측정(`null`)이며 순위·가격·성능 점수는 없습니다.",
        f"- 분모: {definition}",
        "",
        "## 기록",
        "",
        *_table(RECORD_HEADER, [_record_cells(row) for row in report["records"]]),
        "",
        "## 비교 그룹",
    ]
    cohorts = report["cohorts"]
    if not cohorts:
        lines += ["", "- 없음"]
    for number, cohort in enumerate(cohorts, 1):
        rows = [_worker_cells(worker) for worker in cohort["workers"]]
        key = _key(cohort["key"])
        samples = cohort["samples"]
        totals = _totals(cohort["denominator"])
        lines += ["", f"### 그룹 {number}", ""]
        lines += [f"- 비교 키: {key}", f"- 표본 수: {samples}", f"- 분모: {totals}", ""]
        lines += _table(WORKER_HEADER, rows)
    lines += ["", "## 비교 불가", ""]
    if not report["non_comparable"]:
        lines.append("- 없음")
    for item in report["non_comparable"]:
        refs = _refs(item["attempts"])
        key = _key(item["key"])
        reason = item["reason"]
        lines.append(f"- {refs}: {key}. {reason}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("records", nargs="+", help="로컬 JSON 실행 기록 경로")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        report = compare(args.records)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    if args.format == "markdown":
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
