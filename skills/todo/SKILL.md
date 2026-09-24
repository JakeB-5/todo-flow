---
name: todo
description: Investigate a requirement, search existing filesystem tracks and watches for overlap, and register or revise a TODO Flow track document. Registration is separate from execution.
---

# todo — 조사 → 중복 확인 → 문서 등록

프로젝트의 `todo/`(또는 지정된 STATE)가 트랙과 실행 기록의 정본이다. 별도 그래프·벡터 검색이나 데이터베이스 조회 도구를 먼저 배울 필요 없이 `rg`, 파일 읽기로 시작한다. 초기화되지 않은 다른 프로젝트를 임의로 변환하지 않는다.

## 먼저 기존 요구와 근거를 찾는다

- 증상·요구를 분리하고, 원인이 같으면 한 트랙으로 모은다. 질문에 답하는 것만 필요하거나 실제 워커가 수행할 일이 없는 허브 항목은 등록하지 않는다.
- 활성과 완료를 함께 검색한다. 예: `rg -n -i '키워드|관련 함수|증상' todo/tracks -g track.html -g track.md`. 관련 Watch는 `rg -n '키워드|영역' todo/watches`, 판단·기각 이력은 `todo/events/`에서 찾는다. 제목뿐 아니라 현상·원인·파일명·완료 조건으로 후보를 좁힌다. 검색이 잘렸으면 더 좁혀 읽는다.
- 후보의 `track.html`(기존 Markdown 트랙은 `track.md`), `state.json`, 관련 결정·결과를 직접 읽는다. 기존 활성 요구와 같은 작업이면 해당 트랙을 수정하고, 완료 후 회귀·새 요구라면 이전 트랙을 `links`/`derivedFrom`으로 연결한 새 트랙을 작성한다. 기존 완료 이력은 덮어쓰지 않는다. 새로 만드는 형제 항목끼리도 중복을 확인한다.
- 관련 코드를 조사하여 관측 사실·원인 후보·미측정을 구분한다. 중요한 주장은 파일:행과 관측 시점을 남긴다. 원인이 불명확하면 재현·판별할 다음 작업을 명시한다. 범위 밖 발견을 조용히 숨기지 않는다.
- 관련 Watch는 발현이 확인되면 트랙으로 흡수·승격하고 수신처를 연결한다. 단순히 관련 있다는 이유로 해결 처리하지 않는다.

## 실행 가능한 문서를 만든다

사람이 직접 검토할 **HTML 분석·계획 문서**를 정본 밖의 스크래치에 작성한다. [HTML 시작 템플릿](assets/track.html)은 최소 출발점이며 문서의 모양이나 섹션을 제한하지 않는다. `<script type="application/json" id="todo-flow-track">`에는 실행용 목표·조건을 넣고, 사람이 읽는 본문에는 필요한 분석·도해·근거를 작성한다. 실행 메타데이터와 본문이 서로 다른 계획을 말하지 않는지 확인한다.

SVG·이미지·CSS·JavaScript·Canvas·Three.js 시뮬레이션 등 판단에 필요한 표현을 사용한다. 시뮬레이션을 정적 그림이나 텍스트 요약으로 대체하지 않는다. Markdown을 선택하면 [Markdown 템플릿](assets/track.md)으로 작성하며 원문 전체를 보존하고 HTML로 렌더링한다. 자유로운 본문·표·raw HTML도 유지한다. 로컬 자산은 전용 폴더에 모으고 문서에서 `assets/...` 상대 경로로 참조한다. 필수: id/title/goal/scope/evidence/conditions[{id,text,method}]. 조건 ID는 revision 간 유지한다. 필요할 때 priority/area/group, 설계·대안, 의존·trigger, links/derivedFrom, 조사·중복 판단, 위험과 검증을 담는다. 작은 확정 수정은 짧게, 설계·미확정 원인은 충분한 근거를 적는다. 문서 길이·고정 Phase 수를 채우지 않는다.

제품 결정이나 외부 조건이 일부만 막으면 지금 가능한 범위와 기다리는 범위를 구분한다. 같은 파일을 수정한다는 이유만으로 독립 트랙의 착수를 막지 않는다. 완료 조건을 Watch로 옮겨 해소된 것으로 만들지 않는다.

## 등록·검증

`todo-flow --state todo register /tmp/track.html --assets /tmp/track-assets`로 검증·원자 등록한다. JSON 입력도 호환된다. 수정은 현재 `state.json`의 revision을 읽고 `--expected-revision N`을 사용한다. 활성 실행의 목표를 바꾸려면 해당 실행의 정지 상태를 확인한다. 정본 파일을 직접 덮어써 revision·근거를 우회하지 않는다.

등록 후 반환된 `tracks/<id>/track.html`과 `state.json`을 읽고, `rg`로 목표·조건이 검색되는지 확인한다. **문서 등록의 검토에는 실제 브라우저 렌더 확인을 포함한다.** 그림·라벨·이미지를 눈으로 확인하고, 상호작용이 있으면 대표 입력을 조작해 의도한 결과를 보는지 확인한다. 대시보드의 문서 크게 열기 링크로 검토할 수 있다. 이미지·JavaScript 자산도 revision별로 보존되며 이전 revision의 문서가 현재 자산으로 바뀌지 않아야 한다. 파일 생성이나 스키마 통과만으로 시각 검증을 했다고 보고하지 않는다. 렌더/조작이 확인되지 않았으면 미검증 항목을 명시한다. 한 요청의 여러 항목은 각 항목의 조사·등록을 마무리하며 진행한다. 자동 실행·브랜치 전환·외부 이슈 생성·push는 이 스킬의 등록 자체에 포함되지 않는다. 현재 저장소가 Git 커밋을 요구하면 이번 등록 파일만 보존하고 다른 세션 변경을 포함하지 않는다.

결과는 트랙 ID와 문서 경로, 신규/흡수/회귀 판단과 근거, 관련 Watch 처리, 수행한 검증을 짧게 보고한다. 다음은 대시보드 또는 `track-picks`에서 선정한 뒤 `trackrun <id...>`이다.

랜딩 후 track-triage가 등록한 파생 TODO도 같은 문서 계약을 따른다. derivedFrom·triageSource와 중복 판단 근거를 보존하고, 자동 생성된 문서의 documentReview가 pending-human-review이면 시각 검토를 완료했다고 간주하지 않는다. 사용자가 해당 문서를 검토하고 선정하기 전 자동 착수하지 않는다.
