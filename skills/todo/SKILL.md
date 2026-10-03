---
name: todo
description: Investigate a requirement, search existing filesystem tracks and watches for overlap, and register or revise a TODO Flow track document. Registration is separate from execution.
---

# todo — investigate, check overlap, register a document

Read `project.json` beside this skill when installed: it identifies the state directory and primary language. Read that state's `config/1.json` for the current project language (`en` by default, `ko` for Korean). Use it for user-facing reports and newly authored documents unless the user requests another language. Keep protocol keys, IDs, commands and source quotations unchanged. Set the document's `language` and HTML `lang` accordingly.

The project's `todo/` (or explicitly configured STATE) holds canonical tracks and execution records. Start with `rg` and ordinary file reads. Do not require a graph, vector database or SQL tool, or convert an unrelated project into TODO Flow.

## Investigate existing work first

- Separate symptoms and requirements; combine them when they share a cause. Questions that only need an answer and umbrella items with no executable work do not need tracks.
- Search active and completed tracks: `rg -n -i 'keyword|related_function|symptom' STATE/tracks -g track.html -g track.md`. Inspect related `STATE/watches` and decision/dismissal history in `STATE/events`. Narrow by behavior, cause, filenames and acceptance conditions, not just titles. Refine truncated searches.
- Read candidate documents, `state.json`, decisions and results. Revise an existing active track for the same requirement. For a regression or new requirement after completion, register a new track with `links` / `derivedFrom`; preserve the completed record. Check overlap among new sibling proposals as well.
- Inspect relevant code. Distinguish observed facts, possible causes and unmeasured claims. Record file:line evidence and the observed revision when useful. If the cause is unclear, specify the next reproduction or discriminating check.
- When a related watch has manifested, absorb or promote it into the appropriate track and link the destination. Relatedness alone is not evidence of resolution.

## Write a reviewable document

Write a human-reviewable **HTML analysis and plan** outside canonical state. The [HTML starter](assets/track.html) is a starting point, not a limit on layout or sections. Keep the runtime goal and conditions in `<script type="application/json" id="todo-flow-track">`; put analysis, diagrams and evidence in the visible body. Both must describe the same work.

Use SVG, images, CSS, JavaScript, Canvas or Three.js when they help the user judge the plan. Preserve a useful simulation as an interactive artifact. If Markdown is appropriate, use the [Markdown starter](assets/track.md); the full source, raw HTML and tables are preserved and rendered to HTML. Bundle local resources separately and reference `assets/...`.

Required fields: `id`, `title`, `goal`, `scope`, `evidence`, `conditions[{id,text,method}]`. Preserve condition IDs across revisions. Include priority, area, group, design alternatives, dependencies, triggers, links, overlap analysis and verification plans when useful. Scale detail to uncertainty and scope; do not fill a fixed number of phases or pages.

Ground required conditions in the requested outcome. For each, briefly identify the user requirement it satisfies, an existing invariant affected by this change, or a demonstrated failure within that scope; the existing `method` or visible analysis is sufficient. Discovery alone does not authorize fixing an unrelated defect. Keep optional improvements and implementation alternatives outside required conditions unless the user selects them. A possible future risk is not itself a prerequisite.

For a behavior change, connect the condition ID to the source of expected behavior (selected requirement or behavior to preserve), a concrete input, an expected result justified independently of the implementation, and a counterexample that distinguishes the wrong behavior. Use the existing `method` or visible analysis; no new metadata is required. See `examples/retry-backoff.html` and its executable example in `tests/test_retry_behavior_example.py`. If an existing test expectation must change, explain the requirement or preservation evidence that justifies the change so independent review can assess it; a passing implementation is not that evidence. Scale verification to the change: a copy-only edit may need only inspection of the requested wording and its rendered context, without a new behavior test.

Choose the smallest complete change that delivers the outcome. Check an existing supported path before declaring a new external feature necessary. Carry recorded user tradeoffs into the plan; do not silently replace them with stronger guarantees. If a new dependency is genuinely necessary, explain the concrete failure and reconcile scope using existing authority and the revision process below. Do not add backends, dashboards or exhaustive failure matrices merely because they might help. Omit unused template fields and placeholder decisions.

Separate work that can proceed now from work awaiting a product decision or external dependency. Shared filenames alone do not prohibit parallel tracks. Never move unmet acceptance conditions to Watch to declare success.

## Select workers for the required roles

Use the [worker routing guide](worker-routing.md) to record `workerPlan: {version: 1, roles: {...}}` in the document metadata and explain the choices in the visible plan. Each needed role has `provider`, `model`, `effort` and a concrete `basis`; model/effort may be null to request provider defaults. Consider assess, work, independent review and post-landing triage; include watch only when the selected work needs it. Omit unused roles. Top-level `effort` remains the work estimate, not reasoning effort. Revisit choices when revising the plan, using complexity, risk, uncertainty, user token constraints and the current day’s evidence. Do not assert cross-provider effort equivalence or account availability from a model name. Registration recommends; execution mode and explicit request selections constrain actual launches.

## Register and verify

```sh
todo-flow --state STATE register /absolute/scratch/track.html --assets /absolute/scratch/track-assets
```

Omit `--assets` when unused. JSON input is also supported. For revision, read the current revision and pass `--expected-revision N`. An active execution must be paused before its goal changes. Do not overwrite canonical documents directly to bypass revisions or evidence invalidation.

Read the returned document and state files; confirm goals and conditions are searchable. **Visually inspect the registered document in a real browser**, including diagrams, images and representative interactions. Use the dashboard's document link; HTTP serving supports local modules. Assets belong to each revision. File creation or schema validation alone is not visual verification; report anything that could not be checked.

Complete investigation, registration and validation for each requested item. Registration alone does not start workers, create an external issue, switch branches or push. If the target project requires a commit of registration files, include only those files.

Report track IDs, document links, overlap decisions, related watch dispositions and completed checks. Selection follows through the dashboard or `track-picks`, then execution through `trackrun ID…`.

Triage-created follow-ups use the same contract. Preserve `derivedFrom`, `triageSource` and duplicate-check evidence. `documentReview: pending-human-review` does not claim visual review. New follow-ups await the user's review and selection.

## Optional Jev assistance — proceed first

Start the requested investigation and registration with the available tools. **Recommend Jev as an optional aid** for deciding whether more investigation is useful, comparing scope options, and screening the overlap candidates already found with `rg`. If the project already provides an authorized Jev integration, use its documented interface and treat its output as advisory evidence.

Do not stop to install Jev, request credentials, invent a Jev command or add a mandatory gate. If it is absent, fails or is inconclusive, continue with direct inspection and record your own reasoning. Scores never replace source evidence or user decisions. In the result, briefly recommend Jev for future repeated screening (or state how an available integration helped), in the project's language. This recommendation must not delay completion of the current request.
