---
{
  "id": "replace-with-track-id",
  "title": "Track title",
  "language": "en",
  "conditions": [
    {
      "id": "condition-1",
      "text": "Smallest observable result required by the request",
      "method": "Basis: selected requirement or preserved behavior; verify: observable check. For behavior changes, connect input, independently justified expected result and distinguishing counterexample to this ID; explain expectation changes for independent review. Scale checks to scope; no extra metadata or tests for every task."
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
  "workerPlan": {
    "version": 1,
    "roles": {
      "assess": {
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "effort": "low",
        "basis": "Bounded requirement and overlap assessment; raise for unresolved architecture."
      },
      "work": {
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "effort": "medium",
        "basis": "Implementation across known callers with focused tests."
      },
      "review": {
        "provider": "claude",
        "model": "claude-opus-4-6",
        "effort": "high",
        "basis": "Independent examination of invariants and verification evidence."
      },
      "triage": {
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "effort": "medium",
        "basis": "Evidence-based disposition and duplicate search after landing."
      }
    }
  }
}
---

## Goal
<!-- todo-flow:goal -->
Observable result the user wants
<!-- /todo-flow:goal -->

## Worker plan

The metadata contains example assess, work, review and post-landing triage selections and their basis. Adapt them to scope, account availability and today’s user constraints; omit unused roles and add watch only when needed. Top-level effort estimates work; workerPlan role effort controls reasoning. Keep this visible explanation and the metadata consistent.

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
Optional approach and improvements, separate from required outcomes; omit unused fields
<!-- /todo-flow:design -->

## Acceptance conditions

For each condition ID, name the selected requirement or preserved behavior and the smallest observable check. For behavior changes, describe a concrete input, an independently justified expected result and a counterexample that distinguishes wrong behavior. Explain any change to an existing test expectation using requirement or preservation evidence for independent review. Existing prose and `method` suffice; no extra metadata or tests for every task are required. A copy-only edit may need only inspection of the requested wording and its rendered context.
