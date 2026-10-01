# Repository Guidelines

## User-requested scope

Always work only within the scope the user actually requested or explicitly authorized. Do not independently expand the task with additional fixes, cleanup, refactors, or improvements, even when they are related to the requested work.
Perform the verification, review, and delivery needed for that authorized scope. Report newly discovered issues separately; discovering an issue does not authorize fixing it, reopening a completed track, or starting follow-up work.
Once the requested outcome is delivered, stop. Additional work requires an explicit user request. Clearly distinguish completed delivery from any separately authorized follow-up work in status reports.
Report additional work to the user separately: describe the work, why it is needed or optional, whether it blocks the original request, and whether it has been started. Reporting does not authorize execution. Leave work outside the authorized scope unstarted until explicitly requested; do not hide it inside the original task or silently register new tasks. If unrequested work has already been performed, disclose what changed and its current state.
Own the stopping decision: the user must not repeatedly tell you to stop. Before continuing, identify the specific unmet requirement or concrete correctness issue that the next action resolves. If the requested result and required evidence are sufficient, finish without adding checks, improvements, or a permission question. Resolve ordinary ambiguity by choosing the narrower scope; ask only when missing information materially affects correctness, authorization, or an irreversible action. Do not introduce runtime mechanisms or process changes merely to compensate for this judgment. When the user asks to persist a working rule, edit the applicable instructions in that turn and report the actual file changes; a conversational promise is not completion.
For authorized track runs and landings, never disable automatic cleanup in configuration, command flags, temporary drivers, or recovery scripts unless the user explicitly requests retaining those resources. Convenience, debugging, or an intention to clean up later is not authorization. Keep any requested exception scoped and report it. Code landing does not prove resource cleanup: report actual removal or the concrete reason for deferral using cleanup receipts and the relevant Git/Orca inventory, while preserving ownership and user-data protections.
A trackrun is incomplete while any resource created for that run remains pending cleanup. Code landing or a `done` track record is not run completion. Resolve recoverable cleanup deferrals within the authorized run, including unused creation shells and interrupted retirement receipts, then verify absence in both Git and Orca inventories. Do not stop at reporting a deferral or ask the user to request cleanup again. Preserve user changes and reused sessions; if they genuinely block removal, report the run as incomplete with the exact blocker rather than complete. This obligation is cleanup of the selected run, not permission for an unrelated cleanup campaign.

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
