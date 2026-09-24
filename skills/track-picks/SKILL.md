---
name: track-picks
description: Read filesystem TODO tracks and current execution facts to propose work that can start now, needs a decision, or is blocked. Use for trackpicks and candidate selection; never starts workers by itself.
---

# track-picks — 파일을 읽고 실행 후보 선정

기본 정본은 프로젝트 `todo/`, 별도 지정 시 STATE다. 후보 선정은 읽기 전용이며 실행 요청은 `trackrun <track-id...>`으로 분리한다.

1. `rg -n '"status"|"control"|"request"' todo/tracks -g state.json`으로 현재 상태를 좁히고, 후보 `track.html`(또는 기존 `track.md`)의 목표·우선순위·trigger·의존을 읽는다. 특정 영역 요청이면 그 영역부터 찾되 외부 선행 트랙·결정도 확인한다. 전체 파일을 한꺼번에 출력하지 않는다.
2. `todo/tasks`, `todo/decisions`, `todo/watches`, 필요 시 `todo/events`의 관련 ID를 `rg`로 찾아 읽는다. 활성 요청·정지 중인 트랙은 중복 실행 후보에서 제외하고 현황으로 따로 표시한다. 요청 접수와 워커 실행은 다르다. owner·lease·최근 관측이 오래됐으면 현재 활동을 확정하지 않는다.
3. 작성 당시의 “X 완료 후”를 그대로 반복하지 말고 X의 현재 상태와 실제 필요한 산출물을 확인한다. 선행 코드/API가 꼭 필요하면 그 의존을 적고, 문서·결정 관계 또는 마지막 외부 검증만 기다리면 지금 가능한 부분을 분리한다. 필수 미해결 조건을 임의로 Watch에 이월해 완료 가능으로 표시하지 않는다.
4. 같은 파일·area·소비 패키지는 충돌 예상·권장 랜딩 순서·통합 검증 대상이다. 그것만으로 병렬 구현을 금지하지 않는다. 실제 선행 산출물이나 공유 쓰기 공간이 있어야만 작업할 수 있으면 그 구체적 이유로 분리한다.

출력은 트랙 ID를 첫 열로 두고 **지금 가능 / 사용자 결정·입력 필요 / 막힘·미확인**으로 분류한다. 우선도·영역·근거·가능 범위를 짧게 적고, 함께 진행할 후보와 후속 후보를 ID로 열거한다. 그룹명이나 개수로 ID를 대신하지 않는다.

마지막에 `trackrun <추천-id1> <추천-id2>`를 제시한다. 후보가 없으면 빈 실행 명령을 만들지 않는다. 문서·상태·결정을 수정하거나 워커를 띄우지 않는다. 같은 요청에 실행 지시가 명시됐으면 선정한 범위를 유지하여 trackrun 스킬로 이어간다.
