---
name: track-review
description: Independently assess an exact TODO Flow change against condition IDs and verification evidence.
---

# track-review

Use a fresh read-only context, independent of the authoring session. Read the actual files/diff, goal revision and verification evidence. Return summary, verdict (met/unmet/cannot-assess), and exactly one conditions row per condition ID with verdict and evidence. Identify real failures and uncertainties; don't invent test execution.

Request bounded correction work when unmet, land only for an authorized land endpoint, or complete for a review-only endpoint. The runtime publishes your full assessment on the exact PR head as a COMMENT when the GitHub account owns that PR. This is independent agent review, not a GitHub APPROVE from another account.

Record extra observations as findings[{observation,evidence}] or additional condition assessments. After authorized landing, track-triage compares them with actual landed code. A required unmet condition still blocks landing; optional findings do not replace the required verdict.
