# Repository Guidelines

## User-requested scope

Always work only within the scope the user actually requested or explicitly authorized. Do not independently expand the task with additional fixes, cleanup, refactors, or improvements, even when they are related to the requested work.
Perform the verification, review, and delivery needed for that authorized scope. Report newly discovered issues separately; discovering an issue does not authorize fixing it, reopening a completed track, or starting follow-up work.
Once the requested outcome is delivered, stop. Additional work requires an explicit user request. Clearly distinguish completed delivery from any separately authorized follow-up work in status reports.
Report additional work to the user separately: describe the work, why it is needed or optional, whether it blocks the original request, and whether it has been started. Reporting does not authorize execution. Leave work outside the authorized scope unstarted until explicitly requested; do not hide it inside the original task or silently register new tasks. If unrequested work has already been performed, disclose what changed and its current state.
Own the stopping decision: the user must not repeatedly tell you to stop. Before continuing, identify the specific unmet requirement or concrete correctness issue that the next action resolves. If the requested result and required evidence are sufficient, finish without adding checks, improvements, or a permission question. Resolve ordinary ambiguity by choosing the narrower scope; ask only when missing information materially affects correctness, authorization, or an irreversible action. Do not introduce runtime mechanisms or process changes merely to compensate for this judgment. When the user asks to persist a working rule, edit the applicable instructions in that turn and report the actual file changes; a conversational promise is not completion.
For authorized track runs and landings, never disable automatic cleanup in configuration, command flags, temporary drivers, or recovery scripts unless the user explicitly requests retaining those resources. Convenience, debugging, or an intention to clean up later is not authorization. Keep any requested exception scoped and report it. Code landing does not prove resource cleanup: report actual removal or the concrete reason for deferral using cleanup receipts and the relevant Git/Orca inventory, while preserving ownership and user-data protections.
A trackrun is incomplete while any resource created for that run remains pending cleanup. Code landing or a `done` track record is not run completion. Resolve recoverable cleanup deferrals within the authorized run, including unused creation shells and interrupted retirement receipts, then verify absence in both Git and Orca inventories. Do not stop at reporting a deferral or ask the user to request cleanup again. Preserve user changes and reused sessions; if they genuinely block removal, report the run as incomplete with the exact blocker rather than complete. This obligation is cleanup of the selected run, not permission for an unrelated cleanup campaign.

### Reusing confirmed feedback

Keep feedback beside the applicable instruction or regression test, with its source, scope, a public-safe counterexample and a way to check it. This section explains the scope rules above; it is not a separate rule ledger. A confirmed instruction and an implementer's proposal have different authority. Record an explicit user instruction or decision as the source; a commit, implementation summary or repeated suggestion alone does not establish user approval.

Concrete source: the user-supplied worker request for `confirmed-feedback-rules`, revision 3, task `work-16fc2dbe79e04da5`, explicitly instructs: “Tie every proposed change to a selected condition or an existing invariant affected by this change.” It also says: “A useful optional feature, unrelated observed defect or hypothetical risk does not expand this track.” These current instructions reaffirm the existing user-requested scope boundary above. They apply to selecting changes and mandatory follow-up work for the authorized outcome; they do not waive its verification, review, delivery or cleanup obligations. This is a source for the current reaffirmation, not a claim about the original private conversation. `git show 7b48077 -- AGENTS.md` shows the earlier scope guidance and `git show 52de599 -- AGENTS.md` shows its cleanup clarification; those commits establish change history, not user approval. The track's historical `assets/evidence.json` has classification `source-review-and-proposal` and empty `observations`; it establishes neither measured benefits nor user confirmation.

Public counterexample (illustrative, not an observed incident): a user requests a README correction; the implementer writes “also refactor the dispatcher while here.” That note is an unconfirmed proposal, not permission. Deliver the correction and required checks; report the optional refactor separately without starting it. By contrast, the explicit instruction quoted above is an applicable requirement. Review each proposed change against the selected condition or affected invariant and the recorded decisions. Merely labeling a note “confirmed” cannot supply missing authority; retain it as a proposal when the source is absent.

Maintain the existing entry rather than copying it into another registry. For example, clarify an ambiguous “stop after delivery” statement in place to include the selected run's required cleanup, preserving its scope; the cleanup clarification in `52de599` illustrates such an amendment. If a later explicit user decision replaces a rule, revise or remove the superseded wording at that same location, cite the decision and update affected references. Track the change with `git log -p -- AGENTS.md` or the existing track document revisions. A proposed retirement is not effective merely because an implementer suggests it.

Recording a rule does not satisfy an unmet condition or dispose of a finding. For example, documenting “original obligations cannot escape” while an original defect remains still requires correction and the applicable review/triage gates. Existing checks in `tests/test_triage.py` cover this boundary: `test_disposition_batch_is_atomic_and_original_scope_cannot_escape` rejects an in-scope obligation moved to a new track without partial registration, and `test_new_finding_invalidates_clear_receipt_and_old_completion_cannot_skip` shows that a new finding invalidates clearance and blocks completion. These checks protect runtime obligations; they do not prove the authority of a feedback source. For instruction-only changes, inspect the source, scope, counterexample and references through document review. For changed runtime behavior, add or run a focused regression that demonstrates the affected boundary. Preserve configured final verification and independent-review gates in either case; do not add tests that merely assert this prose exists.

## Layout

- `src/todo_flow/`: Python CLI, canonical HTML/Markdown documents and JSON files, disposable query cache, dispatcher, agent/GitHub adapters, loopback dashboard.
- `skills/`: agent-facing skills; packaged into the Python wheel.
- `templates/`: agent-authored track document schema/example.
- `tests/`: unittest store, real local Git integration, HTTP, adapter and large-list projection tests.
- `scripts/`: reproducible public GitHub acceptance tests and labeled local dashboard fixtures.
- `assets/metrics/`: anonymous aggregate measurements, methodology, and generated public charts. Keep raw source histories and identifying metadata outside this repository.
- `docs/`: local-only design specifications, prototypes, and experiment records; ignored by Git and excluded from distributions. Preserve local files and do not force-add them.
- `README.md`, `README.ko.md`, `AGENT_INSTALL.md`, `OPERATIONS.md`, `UPDATES.md`, `DEMO.md`, `CHANGELOG.md`, `CONTRIBUTING.md`: tracked usage, change history, and contributor guidance. Shared instructions must work without `docs/`.

## Commands

`uv sync --frozen` installs the editable package and pinned development tools.
`uv run python -m unittest discover -s tests -v` runs tests.
`uv run ruff check src tests scripts` and `uv run ruff format --check src tests scripts` validate Python.
`uv build` produces the distributable package. See README for runtime setup.

## Invariants

Use descriptive Python identifiers and Ruff formatting. Core runtime uses the standard library; Markdown rendering uses pinned markdown-it-py.
Track authoring belongs to the todo skill/CLI; selection is dashboard/track-picks; execution is trackrun IDs.
Do not add dashboard authoring or execution requests. Track documents are human review artifacts: preserve HTML, SVG, images, JS simulations and revisioned assets; Markdown must render to HTML. Files are authoritative; SQLite is a rebuildable query cache.
A model proposes work; only the fenced host writes files and external effects.
Pass workspace and evidence paths to workers; do not inject repository file bodies into their prompts. Keep built-in exploration read-only. Prefer available visible terminals, preserve actual PID/exit evidence, and never duplicate an uncertain launch through a fallback.
Do not weaken exact-head evidence, independent review, durable handoff, or ownership checks.
Confirmed landing must be triaged before completion; original obligations cannot escape to follow-up TODOs or Watch.
Dispositions, registration and follow-ups commit atomically. New tracks await user selection.
Dashboard lists use bounded server queries; completed tracks have a separate archive.
Queries never mutate execution. UI connection loss never means completion.
Keep credentials, state databases, agent transcripts and local virtualenvs out of Git.

## Changes and tests

Choose the smallest set of checks that establishes the requested outcome. Run fast formatting, lint, and focused behavior checks before expensive full verification; stabilize the change before requesting the required final suite. For instruction-only edits, validate the changed documents or skill metadata rather than automatically running application tests.
Reuse successful evidence for the same candidate and unchanged verification inputs. Repeat or broaden checks only for changed inputs, a failure, a concrete unresolved concern, or an explicit project gate. Independent review should consume existing verification evidence; do not add another review or rerun the suite merely to reconfirm a passing result. Preserve required exact-head, independent-review, and integration gates, and distinguish those requirements from optional extra checks.
Test observable behavior and recovery boundaries, not implementation-shaped snapshots.
External live tests require explicit authorization and use disposable, public-safe fixtures.
Do not alter private/production repositories to exercise test workflows.
Use imperative commit subjects and report validation and real limitations accurately.

## Language and public presentation

English is primary for shared docs, templates and skill instructions. Keep the Korean README current.
Use project `language` for new human-facing worker output and documents, with stable protocol keys.
Dashboard labels use `tr()` / `data-i18n`; never translate authored content or rewrite records on a UI switch.
Keep language preferences isolated by project and preserve selection, drafts and navigation on switching.
Jev is an optional recommendation in todo/watchlist, never an execution or installation prerequisite.
Public demos must disclose synthetic data. The project is MIT licensed.
Run `node --test tests/dashboard_i18n.test.cjs` for localization behavior.

## Update boundaries

Package version and state/config/worker/skill protocol versions are independent. Check compatibility before recovery writes; do not silently downgrade unknown formats. CLI/driver/dashboard lifetimes participate in maintenance locks. Keep engine recovery independent of the environment being replaced. Skill updates compare managed-file baselines, preserve project context and local edits, and reject conflicts before mutation. Preserve durable receipts and test interrupted updates. The isolated update smoke uses synthetic future versions only; never publish its wheels.
