"""Explicitly labeled, local-only dashboard dataset; never imports real project content."""

import argparse
import json
import time
from pathlib import Path

from todo_flow.store import Store, encode

TITLES = [
    (
        "font-selection",
        "폰트 선택 동작 개선",
        "선택한 텍스트에 폰트 변경이 일관되게 적용되도록 합니다.",
        "작업 화면",
    ),
    (
        "export-guide",
        "내보내기 오류 안내",
        "실패 원인과 사용자가 취할 다음 행동을 안내합니다.",
        "작업 화면",
    ),
    (
        "draft-restore",
        "임시저장 복원 안정화",
        "문서를 복원한 뒤 편집 상태와 선택을 유지합니다.",
        "저장",
    ),
    ("paint-order", "페인트 순서 보존", "내보낸 결과에서도 원래 겹침을 유지합니다.", "렌더링"),
    (
        "draft-policy",
        "임시저장 보관 범위 결정",
        "기기별 보관과 계정 공유의 경계를 정합니다.",
        "저장",
    ),
    (
        "selection-restore",
        "선택 상태 복원",
        "문서를 다시 열었을 때 선택 맥락을 복원합니다.",
        "작업 화면",
    ),
    (
        "canvas-align",
        "캔버스 정렬 기준 통일",
        "객체와 아트보드의 정렬 기준을 일치시킵니다.",
        "캔버스",
    ),
    (
        "asset-search",
        "에셋 검색 결과 정리",
        "검색 조건과 선택한 에셋을 명확하게 표시합니다.",
        "에셋",
    ),
]


def seed(store, active=48, completed=2500):
    now = time.time()
    with store.transaction() as c:
        for i in range(active + completed):
            base, title, goal, area = TITLES[i % len(TITLES)]
            done = i >= active
            id_ = f"{base}-{i:04}"
            status = "done" if done else "open"
            control = (
                "finished"
                if done
                else ["idle", "idle", "active", "active", "active", "paused", "idle", "idle"][i % 8]
            )
            title = title if i < 8 else title + f" · {i + 1:04}"
            doc = {
                "id": id_,
                "title": title,
                "goal": goal,
                "scope": "이 화면은 규모 검증용 예시입니다. 목표와 관련된 변경만 포함합니다.",
                "evidence": "합성된 UI 테스트 데이터. 실제 프로젝트나 실행을 나타내지 않습니다.",
                "priority": ["높음", "보통", "높음", "높음", "보통", "보통", "낮음", "보통"][i % 8],
                "area": area,
                "conditions": [
                    {
                        "id": "behavior",
                        "text": "요구한 동작을 확인할 수 있다.",
                        "method": "해당 시나리오와 결과 대조",
                    },
                    {"id": "regression", "text": "기존 동작을 유지한다.", "method": "회귀 검증"},
                ],
            }
            updated = now - (i - active) * 3600 if done else now - i * 60
            review = {
                "verdict": "met",
                "conditions": [
                    {"id": x["id"], "verdict": "met", "evidence": "규모 검증용 예시 판정"}
                    for x in doc["conditions"]
                ],
            }
            verification = {
                "ok": True,
                "output": "SYNTHETIC_LARGE_EVIDENCE\n" * 200,
                "head": f"{i:040x}",
            }
            c.execute(
                "INSERT INTO tracks(id,revision,document,status,control,head,verification,review,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    id_,
                    1,
                    encode(doc),
                    status,
                    control,
                    f"{i:040x}" if done else None,
                    encode(verification) if done else None,
                    encode(review) if done else None,
                    updated,
                ),
            )
            if not done and control == "active":
                waiting = i % 8 == 4
                kind = "review" if i % 8 == 3 else "work"
                work = "work-demo-" + str(i)
                c.execute(
                    "INSERT INTO tasks(id,track,kind,purpose,status,owner,lease,generation,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        work,
                        id_,
                        kind,
                        "완료 조건과 변경을 대조합니다."
                        if kind == "review"
                        else "복원 동작의 원인과 검증 환경을 확인합니다.",
                        "waiting" if waiting else "running",
                        None if waiting else "worker-demo-" + str(i),
                        None if waiting else now + 3600,
                        1,
                        updated,
                        now - 15,
                    ),
                )
                if waiting:
                    c.execute(
                        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                        (
                            "decision-demo-" + str(i),
                            id_,
                            work,
                            "임시저장을 기기별로 보관할까요, 계정별로 공유할까요? 이 결정이 필요한 작업만 기다립니다.",
                            "open",
                            1,
                            None,
                            updated,
                        ),
                    )
            if i < 40:
                store.event(
                    c,
                    "work.result" if done else "document.registered",
                    id_,
                    {"summary": "규모 검증용 예시 기록"},
                )
        c.execute(
            "INSERT INTO watches VALUES(?,?,?,?,?,?,?)",
            (
                "watch-demo",
                TITLES[0][0] + "-0000",
                encode(
                    {
                        "observation": "특정 템플릿 객체의 폰트 변경 경로 확인",
                        "reason": "재현 조건 미확정",
                        "trigger": "해당 템플릿에서 증상 재현",
                        "next_action": "원본 선택 맥락을 수집하고 필요한 조사 작업으로 연결",
                    }
                ),
                "open",
                "template-reproduced",
                None,
                now,
            ),
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    args = p.parse_args()
    state = Path(args.state)
    if state.exists():
        p.error("Use a new state directory")
    store = Store(state)
    store.configure(
        {
            "github": None,
            "base": "main",
            "endpoint": "review",
            "display_name": "TODO Flow · 규모 검증",
            "demo": True,
        }
    )
    seed(store)
    print(
        json.dumps(
            {"state": str(state.resolve()), "active": 48, "completed": 2500, "synthetic": True}
        )
    )


if __name__ == "__main__":
    main()
