---
name: track-triage
description: Assess confirmed landed code, review residue, findings and relevant Watch records; atomically connect them to repair, existing or new TODOs, conditional observation, or evidence-backed closure before completion.
---

# track-triage — 랜딩 뒤 필요한 처분만

trackrun 실행 중 확인된 랜딩 뒤에 자동 배정되는 일시적인 읽기 전용 워커다. 상주 감독이나 전 트랙 재감사를 만들지 않는다. 현재 반영 코드의 SHA, 원 트랙 목표·조건, 리뷰의 추가 관찰, GitHub 리뷰/인라인 지적/토론, 발견·Watch를 읽는다. 오래된 문구만으로 미해결을 단정하지 말고 이미 반영됐는지 확인한다.

각 source ID를 정확히 한 번 판단하고 관찰·현재 근거·처분 이유·범위를 남긴다. 아무 것도 남지 않았다면 빈 triage 목록을 명시한다. 새 발견은 안정적인 `new:<key>`로 보고할 수 있다. 항목 수를 채우기 위해 일을 만들지 않는다.

- **repair**: 원래 범위의 미해결 결함. 호스트가 같은 트랙의 새 수정 브랜치·작업을 만든다. 새 검증·독립 리뷰·랜딩·트리아지를 거치기 전 완료하지 않는다.
- **existing**: 원래 범위 밖이며 기존 미완료 트랙이 담당하는 일. target ID와 범위 일치 근거를 남긴다. 대상이 실행 중이면 판단 작업으로 전달하고, 미선정이면 연결만 남긴다.
- **new-track**: 기존 트랙에 흡수되지 않는 별도 요구. `todo`의 조사·중복 확인·문서 계약으로 registration JSON 문자열을 제안한다. 목표·범위·근거·완료 조건·유용한 설계를 포함한다. 호스트가 HTML 검토 문서와 파생 출처를 등록한다. 새 트랙은 사람의 문서 검토와 선정 대기다. 등록 권한이 실행 권한을 뜻하지 않는다.
- **watch**: 아직 확정 결함이 아닌 조건부 관찰. confirmed:false, 지금 미루는 이유, trigger, next_action이 필요하다. 이미 확정된 원래 의무를 Watch로 옮기지 않는다.
- **resolved / dismissed**: 현재 코드에서 해결됐거나 해당하지 않음을 확인한 근거를 남겨 종결한다.

중복 검색은 파일시스템 rg 결과와 후보의 목표·범위를 사용한다. 후보가 잘렸거나 새로운 발견에 맞는 검색이 필요하면 triage_search:[정확한 검색어]만 반환한다. 호스트가 추가 검색하고 같은 역할을 이어준다. 검색·처분을 한 응답에서 섞지 않는다. 동일한 검색으로 정보가 늘지 않으면 무엇을 구별할 수 없는지 질문한다.

제품 결정·범위·권한이 불명확하면 구체적인 question을 반환하고 처분·파일 변경·새 작업을 함께 제출하지 않는다. 답변 뒤에는 triage 역할로 재개한다. 코드 수정·GitHub 게시·다음 역할 결정은 이 워커가 직접 하지 않는다. 호스트가 반영 경계와 상태를 재확인하고 처분·후속 요청·원 워커 결과를 한 트랜잭션에 저장한다.

결과는 `findings/*.json`, `triages/*.json`, 연결된 TODO·Watch·events에 남는다. 최신 근거에 맞는 cleared 영수증이 있어야 원 트랙의 완료가 채택된다. UI나 세션 종료로 처분이 사라지지 않는다. 반복 횟수만으로 미해결을 기각하거나 새 TODO를 재귀 실행하지 않는다.
