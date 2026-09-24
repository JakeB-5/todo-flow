# Repository Guidelines

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
