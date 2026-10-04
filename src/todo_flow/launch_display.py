"""Read-only presentation of durable launcher selection, never process liveness."""

import json
from pathlib import Path
import re

from .launch_locales import MESSAGES


BACKENDS = {
    "orca": ("Orca terminal (command worker)", "Orca 터미널(명령 워커)"),
    "headless": ("Headless process", "Headless 프로세스"),
    "tmux": ("tmux terminal", "tmux 터미널"),
    "terminal": ("Configured terminal", "설정된 터미널"),
}
REASONS = {
    "claude_interactive_supported": (
        "Interactive Claude session selected",
        "Claude 대화형 세션 선택됨",
    ),
    "claude_executable_missing": ("Claude executable not found", "Claude 실행 파일 없음"),
    "claude_interactive_contract_unavailable": (
        "Installed Claude lacks the required interactive isolation flags",
        "설치된 Claude에 필요한 대화형 격리 옵션이 없음",
    ),
    "native_supported": ("Supported native route selected", "지원되는 Native 세션 경로 선택"),
    "native_managed_workspace_required": (
        "Existing checkout has no managed ownership receipt",
        "기존 작업공간의 Orca 관리 소유권 기록 없음",
    ),
    "native_codex_missing": ("Codex executable not found", "Codex 실행 파일 없음"),
    "native_codex_version_unverified": (
        "Codex native protocol version unverified",
        "Codex Native 프로토콜 버전 미검증",
    ),
    "native_contract_probe_failed": (
        "Native workspace contract check failed",
        "Native 작업공간 계약 확인 실패",
    ),
    "native_auth_storage_unsupported": (
        "Authentication storage unsupported by native adapter",
        "Native 어댑터에서 지원하지 않는 인증 저장 방식",
    ),
    "native_review_provenance_unavailable": (
        "Native implementation session provenance unavailable",
        "Native 구현 세션의 출처 기록 없음",
    ),
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
    "failed": ("Worker failed; recovery required", "워커 실패; 복구 필요"),
    "reconciling": (
        "Reading the same session history; input is not resent",
        "같은 세션 기록 확인 중; 입력 재전송 안 함",
    ),
    "server-intent": ("Server launch recorded; start pending", "서버 실행 기록됨; 시작 대기"),
    "server-started": ("Server process started", "서버 프로세스 시작됨"),
    "thread-created": ("Read-only session created", "읽기 전용 세션 생성됨"),
    "turn-accepted": ("Work turn accepted", "작업 요청 수락됨"),
    "viewer-accepted": ("Visible session client requested", "표시용 세션 클라이언트 요청됨"),
    "proposal-received": ("Complete proposal received", "전체 제안 수신됨"),
    "server-stopped": (
        "Server stopped; group confirmation pending",
        "서버 종료됨; 프로세스 그룹 확인 대기",
    ),
    "cleanup-failed": ("Cleanup requires reconciliation", "정리 상태 재확인 필요"),
    "complete": (
        "Proposal saved; supervisor confirmation pending",
        "제안 저장됨; 감독 프로세스 확인 대기",
    ),
    "completed": ("Proposal and process cleanup confirmed", "제안 및 프로세스 정리 확인됨"),
    "selected": ("Selected; process start not established", "선택됨; 프로세스 시작 근거 아님"),
    "unavailable": ("Selection failed before launch", "실행 전 선택 실패"),
    "launching": ("Launch requested; outcome pending", "실행 요청 중; 결과 미확인"),
    "accepted": (
        "Terminal request accepted; worker start not established",
        "터미널 요청 수락됨; 워커 시작 근거 아님",
    ),
    "unconfirmed": (
        "Launch outcome unconfirmed; do not duplicate",
        "실행 결과 미확인; 중복 실행 금지",
    ),
}


def describe_launch(record, language="en"):
    """Translate only runtime labels; preserve unknown codes without guessing."""

    def text(english, korean):
        if language in ("ja", "zh-CN"):
            return MESSAGES.get(english, (english, english))[int(language == "zh-CN")]
        return korean if language == "ko" else english

    def label(mapping, value):
        return text(*mapping[value]) if value in mapping else str(value)

    selection = record.get("selection") or {}
    backend = record.get("backend")
    sidebar = record.get("sidebar") or {}
    sidebar_confirmed = isinstance(sidebar, dict) and sidebar.get("status") == "confirmed"
    unknown = text("Not recorded", "기록 없음")
    return {
        "requested": selection.get("requested") or unknown,
        "backend": (
            text("Orca Codex sidebar session", "Orca Codex 사이드바 세션")
            if sidebar_confirmed
            else text("Orca Codex terminal client", "Orca Codex 터미널 클라이언트")
        )
        if record.get("execution_mode") == "orca-native"
        else text("Orca Claude terminal session", "Orca Claude 터미널 세션")
        if record.get("execution_mode") == "orca-interactive"
        else label(BACKENDS, backend)
        if backend
        else text("No backend selected", "선택된 backend 없음"),
        "reason": label(REASONS, selection["reason"]) if selection.get("reason") else unknown,
        "status": label(STATUSES, record["status"]) if record.get("status") else unknown,
        "native": text(
            "Interactive Claude; completed session records supply the proposal."
            if record.get("execution_mode") == "orca-interactive"
            else "Host-owned App Server; sidebar lifecycle is projected by the host."
            if selection.get("native_ready")
            else "Compatibility worker route selected."
            if backend
            else "Native session selection not recorded.",
            "Claude 대화형 실행; 완료된 세션 기록에서 제안을 수집함."
            if record.get("execution_mode") == "orca-interactive"
            else "호스트 소유 App Server 사용; 호스트가 사이드바 세션 상태를 전달함."
            if selection.get("native_ready")
            else "호환 워커 경로 선택됨."
            if backend
            else "Native 세션 선택 기록 없음.",
        ),
        "validation": text(
            "Real-model and external live tests for this integration have not been performed.",
            "이 통합의 실제 모델 및 외부 live 시험은 미실시입니다.",
        ),
        "process": text(
            "Selection and terminal acceptance do not prove worker start or completion.",
            "선택 및 터미널 요청 수락은 워커 시작이나 완료의 근거가 아닙니다.",
        ),
        "sidebar": (
            text("Sidebar session observed", "사이드바 세션 표시 확인됨")
            if sidebar_confirmed
            else text("Sidebar session not confirmed", "사이드바 세션 표시 미확인")
        ),
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
        for key in ("backend", "status", "session", "turn", "execution_mode"):
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
            for key in (
                "backend",
                "status",
                "selection",
                "worktree",
                "terminal",
                "handle",
                "session",
                "turn",
                "execution_mode",
                "sidebar",
            )
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
