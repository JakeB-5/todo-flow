---
name: trackrun
description: Execute explicitly selected TODO track IDs with headless workers, durable filesystem handoffs and separate worktrees. Use for trackrun id1 id2 requests after dashboard or track-picks selection.
---

# trackrun — 선택한 트랙 실행

`trackrun <track-id1> <track-id2>`가 실행 진입점이다. 프로젝트 기본 정본은 `todo/`이며 다른 위치면 `--state /absolute/STATE`를 사용한다. 설치 전에는 `uv run trackrun ...`을 쓴다.

선택 ID의 `tracks/<id>/track.html`(또는 기존 `track.md`)·`state.json`과 프로젝트 `config/1.json`을 읽어 사용자가 선택한 범위와 이미 설정한 검증·반영 권한을 확인한다. 이전에 주어진 권한을 다시 묻지 않는다. 후보 추천만 받은 경우에는 실행하지 않는다.

명령은 요청을 파일에 저장한 뒤 실행 장치를 시작해 현재 가능한 작업을 처리한다. 각 assess/work/review는 새 헤드리스 에이전트이며 결과와 다음 작업을 파일에 보존한다. 목표가 끝나거나 사용자 결정 등으로 실행 가능한 일이 없어지면 명령이 끝난다. 기본 동시성은 2이며 `--jobs N`으로 지정한다. 별도 상주 감독 에이전트는 없다.

이미 별도 `todo-flow --state STATE run --daemon`을 운영 중이면 `trackrun ... --request-only`로 접수만 할 수 있다. 한 번의 작업 상한에 도달하면 요청은 남으며 `todo-flow --state STATE run`으로 이어간다. `--max-tasks`는 목표 완료 판정 기준이 아니다.

실제 실행 여부는 `tasks/*.json`·`attempt-records/*.json`, 결과는 `results/*.json`, 사용자 질문은 `decisions/*.json`, 외부 반영은 `effects/*.json`에서 확인한다. 캐시를 SQL로 조회할 필요가 없다. 파일 상태가 미확인이면 성공으로 보고하지 않는다.

사용자 지시로 정지·재개·취소할 때는 `todo-flow --state STATE pause|resume|cancel ID`. 결정 답변은 `answer DECISION_ID --text TEXT`로 남기고 필요한 실행기를 다시 시작한다. 요청·인계·효과 영수증을 지우거나 실패한 런을 무개입 성공으로 보고하지 않는다.

랜딩이 허용된 프로젝트는 반영 뒤 [track-triage](../track-triage/SKILL.md)가 자동으로 잔여·발견·관련 Watch를 처분한다. 원래 범위의 결함은 새 수정 브랜치에서 재작업·리뷰·랜딩으로 돌아가며, 별도 후속 TODO는 HTML 문서로 등록하고 선정 대기에 둔다. 최신 트리아지 영수증 없이는 이슈 종료·완료를 채택하지 않는다. 사용자 질문은 해당 역할로 재개하며 다른 독립 트랙은 계속 진행할 수 있다.
