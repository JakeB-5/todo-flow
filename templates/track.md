---
{
  "id": "replace-with-track-id",
  "title": "Track title",
  "conditions": [
    {
      "id": "condition-1",
      "text": "A verifiable result",
      "method": "How to confirm it"
    }
  ],
  "group": "product",
  "area": [
    "text"
  ],
  "priority": "HIGH",
  "trigger": "착수 별도 지시. 선행 없음.",
  "effort": {
    "estimate": "범위별 예상",
    "basis": "추정 근거"
  },
  "links": [],
  "derivedFrom": [],
  "dependencies": [],
  "decisionRequests": [
    {
      "id": "decision-if-needed",
      "question": "실질적인 선택이 필요한 경우에만 작성",
      "owner": "user",
      "unlocks": "이 결정이 여는 구체적인 범위",
      "beforeDecision": "결정 전에 진행할 수 있는 범위"
    }
  ],
  "duplicateCheck": {
    "queries": [
      "증상",
      "관련 함수"
    ],
    "candidates": [],
    "decision": "new",
    "reason": "기존 트랙과 구별되는 요구"
  },
  "watchRefs": [],
  "history": [
    {
      "date": "YYYY-MM-DD",
      "kind": "created",
      "note": "조사 근거·중복 판정 요약"
    }
  ],
  "routingAdvice": {
    "tier": "M",
    "basis": "작업 성격과 범위에 따른 참고 판단. 모델 설정은 프로젝트 실행 계약에서 결정"
  }
}
---

## 목표
<!-- todo-flow:goal -->
Observable result the user wants
<!-- /todo-flow:goal -->

## 범위
<!-- todo-flow:scope -->
Included work and explicit exclusions
<!-- /todo-flow:scope -->

## 문제와 근거
<!-- todo-flow:evidence -->
Request, observation or source that establishes the problem
<!-- /todo-flow:evidence -->

## 접근과 결정
<!-- todo-flow:design -->
Optional decisions, hypotheses and rationale; do not copy runtime status here
<!-- /todo-flow:design -->
