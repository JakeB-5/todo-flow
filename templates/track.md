---
{
  "id": "replace-with-track-id",
  "title": "Track title",
  "language": "en",
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
  "trigger": "Start only after explicit selection. No prerequisites.",
  "effort": {
    "estimate": "Estimate by scope",
    "basis": "Estimation basis"
  },
  "links": [],
  "derivedFrom": [],
  "dependencies": [],
  "decisionRequests": [
    {
      "id": "decision-if-needed",
      "question": "Include only when a meaningful decision is needed",
      "owner": "user",
      "unlocks": "Specific scope unlocked by this decision",
      "beforeDecision": "Work that can proceed before the decision"
    }
  ],
  "duplicateCheck": {
    "queries": [
      "symptom",
      "related function"
    ],
    "candidates": [],
    "decision": "new",
    "reason": "Requirement distinct from existing tracks"
  },
  "watchRefs": [],
  "history": [
    {
      "date": "YYYY-MM-DD",
      "kind": "created",
      "note": "Investigation evidence and overlap decision"
    }
  ],
  "routingAdvice": {
    "tier": "M",
    "basis": "Advisory assessment of work and scope. Model selection belongs to project configuration."
  }
}
---

## Goal
<!-- todo-flow:goal -->
Observable result the user wants
<!-- /todo-flow:goal -->

## Scope
<!-- todo-flow:scope -->
Included work and explicit exclusions
<!-- /todo-flow:scope -->

## Problem and evidence
<!-- todo-flow:evidence -->
Request, observation or source that establishes the problem
<!-- /todo-flow:evidence -->

## Approach and decisions
<!-- todo-flow:design -->
Optional decisions, hypotheses and rationale; do not copy runtime status here
<!-- /todo-flow:design -->
