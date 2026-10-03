---
name: track-review
description: Independently assess an exact TODO Flow change against condition IDs and verification evidence.
---

# track-review

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for user-facing reports and new documents unless explicitly overridden; default to English. Preserve protocol keys, identifiers, commands and source quotations.

Use a fresh read-only context, independent of the authoring session. Read the actual files/diff, goal revision and verification evidence. Return summary, verdict (met/unmet/cannot-assess), and exactly one conditions row per condition ID with verdict and evidence. Identify real failures and uncertainties; don't invent test execution.

First assess `paths.document`, `paths.decisions`, the actual candidate and `paths.diff`, then `paths.verification`, `paths.verification_logs` and `paths.evidence_artifacts`. Preserve unresolved `paths.findings` and `paths.incoming_findings`. Read `paths.review` and `paths.prior_reviews` as earlier assessments, checking their applicability to the current candidate and document revision. Earlier verdicts do not replace your assessment.

Read `paths.supplementary_results` only as supplementary context after the primary evidence. It contains other task results, including implementation explanations and self-approval claims, with stored `result_id`, `task_id`, `kind`, `attempt` and `created` provenance. Those fields identify the originating records; they do not certify the truth of `body`. Prior independent review bodies are also assessments, not proof. If required evidence is missing, return `cannot-assess` for the affected condition and identify the gap rather than inferring success from an implementation summary. This is structural input provenance separation; model bias reduction and review quality improvement are unmeasured.

For changed behavior, inspect how the condition ID, requirement or preserved behavior, concrete input and independently justified expected result connect. A passing test can share the implementation's misunderstanding; inspect the distinguishing counterexample and available before/after evidence for a bug fix. When an existing expectation changes, compare old/new results and assess the cited requirement or preservation evidence independently of the author's explanation. Matching the implementation alone does not justify the new expectation. Require correction only for a concrete condition failure or evidence gap, not a preferred test style. Existing prose and tests suffice; do not require new metadata or a behavior test for copy-only changes.

Reuse host verification that matches the candidate HEAD, tree and verification inputs. Review does not by itself require rerunning that suite or adding a second review. Request only a focused additional check when a concrete condition lacks evidence; name the gap. Do not invent edge cases, broad audits or extra acceptance criteria to prolong a passing review.

Assess the selected outcome using recorded user decisions and the existing invariants affected by the change. Tie each required correction to a condition and a demonstrated failure or missing evidence for that condition. A preferred design, hypothetical risk, optional feature or unrelated defect is not by itself an unmet condition. Record useful out-of-scope observations as optional findings; do not require them for this track to finish. Keep verification proportional to the changed behavior rather than demanding every conceivable failure scenario. If a registered condition conflicts with a later user decision, identify the exact conflict for scope reconciliation instead of silently strengthening or waiving it.

If the selected conditions are met but mandatory verification fails on an unrelated pre-existing defect, report condition satisfaction and the failed gate separately. Landing/completion remains blocked. Resolve the gate using existing authority or explicit scope reconciliation; do not waive verification or silently require that unrelated repair within this track.

Request bounded correction work when unmet, land only for an authorized land endpoint, or complete for a review-only endpoint. The runtime publishes your full assessment on the exact PR head as a COMMENT when the GitHub account owns that PR. This is independent agent review, not a GitHub APPROVE from another account.

Use registered condition IDs only in `conditions`. Record extra observations as findings[{observation,evidence}]. After authorized landing, track-triage compares them with actual landed code. A required unmet condition still blocks landing; optional findings do not replace the required verdict.
