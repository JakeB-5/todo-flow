"""Read-only presentation of durable launcher selection, never process liveness."""

import json
from pathlib import Path
import re


BACKENDS = {
    "orca": ("Orca terminal (command worker)", "Orca 터미널(명령 워커)"),
    "headless": ("Headless process", "Headless 프로세스"),
    "tmux": ("tmux terminal", "tmux 터미널"),
    "terminal": ("Configured terminal", "설정된 터미널"),
}
REASONS = {
    "explicit_headless": ("Headless explicitly requested", "Headless 명시적 요청"),
    "explicit_launcher": ("Launcher explicitly requested", "실행 방식 명시적 요청"),
    "cli_missing": ("Orca CLI not found", "Orca CLI를 찾을 수 없음"),
    "orca_unavailable": ("Orca terminal unavailable", "Orca 터미널 사용 불가"),
    "remote_host_mismatch": ("Orca host is not local", "Orca host가 로컬이 아님"),
    "host_unverified": ("Orca host could not be verified", "Orca host 확인 불가"),
    "discovery_failed": ("Orca capability discovery failed", "Orca 기능 조사 실패"),
    "discovery_command_unsupported": (
        "Installed Orca does not support capability discovery",
        "설치된 Orca가 기능 조사 명령을 지원하지 않음",
    ),
    "schema_unrecognized": ("Orca command schema not recognized", "Orca 명령 스키마 인식 불가"),
    "native_contract_unverified": (
        "Native session contract remains unverified",
        "Native 세션 계약 검증 미완료",
    ),
}
STATUSES = {
    "selected": ("Selected; process start not established", "선택됨; 프로세스 시작 근거 아님"),
    "unavailable": ("Selection failed before launch", "실행 전 선택 실패"),
    "launching": ("Launch requested; outcome pending", "실행 요청 중; 결과 미확인"),
    "accepted": (
        "Terminal request accepted; worker start not established",
        "터미널 요청 수락됨; 워커 시작 근거 아님",
    ),
    "unconfirmed": ("Launch outcome unconfirmed; do not duplicate", "실행 결과 미확인; 중복 실행 금지"),
}


def describe_launch(record, language="en"):
    """Translate only runtime labels; preserve unknown codes without guessing."""
    index = int(language == "ko")

    def label(mapping, value):
        return mapping[value][index] if value in mapping else str(value)

    selection = record.get("selection") or {}
    backend = record.get("backend")
    unknown = ("Not recorded", "기록 없음")[index]
    return {
        "requested": selection.get("requested") or unknown,
        "backend": label(BACKENDS, backend)
        if backend
        else ("No backend selected", "선택된 backend 없음")[index],
        "reason": label(REASONS, selection["reason"]) if selection.get("reason") else unknown,
        "status": label(STATUSES, record["status"]) if record.get("status") else unknown,
        "native": (
            "Native session adapter is not implemented.",
            "Native 세션 어댑터는 미구현 상태입니다.",
        )[index],
        "validation": (
            "Real-model and external live tests for this integration have not been performed.",
            "이 통합의 실제 모델 및 외부 live 시험은 미실시입니다.",
        )[index],
        "process": (
            "Selection and terminal acceptance do not prove worker start or completion.",
            "선택 및 터미널 요청 수락은 워커 시작이나 완료의 근거가 아닙니다.",
        )[index],
    }


def read_launch(state, attempt, language="en"):
    """Read one bounded receipt; missing or partial writes are explicitly unknown."""
    result = {"attempt": attempt, "evidence": "missing", "record": None, "summary": None}
    if attempt is None:
        return result
    if not isinstance(attempt, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", attempt):
        raise ValueError("Invalid attempt ID")
    path = Path(state) / "attempts" / attempt / "launch.json"
    try:
        with path.open(encoding="utf-8") as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError("Launch evidence exceeds display limit")
        record = json.loads(text)
        if not isinstance(record, dict):
            raise ValueError("Invalid launch evidence")
        for key in ("backend", "status"):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise ValueError("Invalid launch label")
        selection = record.get("selection")
        if selection is not None:
            if not isinstance(selection, dict):
                raise ValueError("Invalid launch selection")
            for key in ("requested", "backend", "reason"):
                if selection.get(key) is not None and not isinstance(selection[key], str):
                    raise ValueError("Invalid selection label")
        # Do not expose configured argv, socket paths or arbitrary extra fields.
        visible = {
            key: record[key]
            for key in ("backend", "status", "selection", "worktree", "terminal", "handle")
            if key in record
        }
        result.update(
            evidence="available", record=visible, summary=describe_launch(visible, language)
        )
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, ValueError):
        result["evidence"] = "unreadable"
    return result


def task_launch(store, task, language=None):
    """Resolve the latest attempt in SQL; never reuse an older attempt's receipt."""
    with store.connect() as connection:
        exists = connection.execute("SELECT id FROM tasks WHERE id=?", (task,)).fetchone()
        if not exists:
            raise ValueError("Unknown task")
        attempt = connection.execute(
            "SELECT id FROM attempts WHERE task=? ORDER BY started DESC,id DESC LIMIT 1",
            (task,),
        ).fetchone()
    return read_launch(
        store.path,
        attempt["id"] if attempt else None,
        language or store.config().get("language", "en"),
    )
