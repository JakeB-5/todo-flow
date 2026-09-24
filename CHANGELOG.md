# Changelog

User-visible behavior, compatibility, important fixes and repository changes. The first public release is `0.0.1`, distributed through GitHub Releases.

## 0.0.2 — 2026-09-24

### Fixed

- Complete the resource lifecycle after delivery: clean owned implementation/integration/triage worktrees and confirmed idle worker terminals, retain evidence and branches, and journal deferred or interrupted cleanup for retry without reopening the track.
- Commit task completion and track completion atomically so reconciliation cannot reopen finished work in between. Preserve the candidate SHA separately from the triage checkout SHA.

### Validation

- Upgraded an isolated installation from the published `0.0.1` wheel to `0.0.2`, updated and rolled back project skills, then rolled back the engine. Canonical state and project language bindings remained unchanged.
- Real Codex workers in 14 Orca terminals completed three parallel tracks through independent reviews, GitHub PR merges, triage and issue closure in a fresh public fixture, without decisions or runtime errors. Follow-up documentation was registered/linked and left unselected. See the [run artifacts](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924).

### Changed

- Path-based worker protocol 2: run in the assigned checkout with read-only search/read tools; pass document, diff and evidence paths instead of source snapshots. Remove the aggregate 150 KB source cap and diff truncation. Legacy custom adapters must adopt protocol 2 explicitly.
- Prefer visible Orca terminals, configured terminal launchers or an existing tmux session; use headless when unavailable or explicitly selected. Preserve streamed logs, terminal handles, worker PIDs and completion receipts, and stop uncertain launches without spawning duplicates.
- Skip the CI matrix for root Markdown guides and presentation assets alone. Check `main` pushes and pull requests, avoid duplicate tag/feature-branch push runs, cancel superseded runs and allow manual full checks.

## 0.0.1 — 2026-09-24

### Added

- English and Korean workflow-cycle diagrams covering todo, trackpicks, trackrun, watchlist and follow-up selection.
- Installed version reporting for both CLI entrypoints, explicit release compatibility contracts and rejection of unknown state/config/worker/skill formats.
- Guarded uv-tool engine upgrades from a supplied newer wheel, environment/entrypoint backups, rollback, and an independent runner for recovery when an update interrupts the CLI.
- Per-project skill installation manifests, dry-run updates, user-edit/conflict handling, legacy baseline adoption, retired-resource handling and recoverable rollback.
- Cooperative runtime/update locks, known-project compatibility preflight, and blocking for unresolved running work or recorded live workers.
- Update operations guide, a prioritized future release checklist and isolated real uv-tool update/rollback/failure acceptance tests.

- English-first README with a Korean companion, product demonstration, first-run walkthrough, support matrix and FAQ.
- Dedicated agent installation, operations and demo guides that work without ignored local design notes.
- MIT license and package license metadata.
- Project language choice during initialization and standalone skill installation: English or Korean. Interactive CLI prompts; unattended CLI defaults to English; installation agents ask when no preference is available.
- English/Korean dashboard messages, accessible labels, localized dates/counts and a project-specific browser display preference.
- Project language in installed skill context, built-in worker instructions, custom-adapter input and generated follow-up documents.
- Optional, nonblocking Jev recommendations for todo investigation/overlap screening and watchlist changed-source review.
- Public-safe HTML track examples and reproducible dashboard captures.
- CI configuration for local tests, Python lint/format, dashboard localization and package builds on Linux and macOS.
- `todo-flow` CLI and `trackrun` entrypoint for selected durable work requests.
- Project-installed todo, track-picks, work, review, landing, triage and Watch skills.
- HTML/Markdown/JSON registration with preserved SVG, images, JavaScript assets and document revisions.
- Searchable file authority, rebuildable query cache and migration from legacy SQL state.
- Parallel tracks in separate worktrees; ownership, durable handoffs, questions, pause/resume/cancel and recovery.
- Independent Claude/Codex workers and exact-candidate review.
- GitHub issues, PRs and reviews; verified integration commits, effect receipts and reconciliation.
- Post-landing triage for original-scope repairs, existing/new TODOs, conditional watches and evidence-based closure.
- Continuous active TODO lists, separate completed search, activity and evidence views.
- Local tests, disposable public acceptance scripts and synthetic large-list fixtures.
- A 33× daily commit activity visual with collapsible measurement notes and anonymous January–September aggregates.

### Changed

- Prepare package metadata and public version references for the first `0.0.1` release.

- Public skill instructions and starter templates now use English. New user-facing content follows the selected project language; existing documents are preserved.
- README prioritizes product outcomes, actual UI, quick start and an agent installation prompt; detailed operation has moved to shared root guides.
- Tracks and runtime records use files as authority; SQLite is disposable.
- Follow-up registration remains separate from selection; completion and issue closure require current cleared triage.
- `docs/` remains local-only and excluded from Git and distributions. Shared usage and contribution guidance does not depend on it.
- Public documents, examples and metric assets omit identifying source-project metadata.

### Fixed

- Install ripgrep explicitly in CI instead of relying on runner images; use current action releases and a validated uv version.

- Triage duplicate search no longer mistakes historical `revisions/` documents for current track IDs.
- Post-landing repair reopens the issue and distinguishes closure receipts for the new delivery cycle.

### Validation of update support

- 81 Python tests and six dashboard localization tests passed; the update cases include conflicts, exact rollback, interrupted recovery, retired skills, protected context, unsupported formats and maintenance exclusion.
- Isolated real uv tool acceptance exercised `0.0.1` to a synthetic `0.0.2`, active-dashboard blocking, skill update/rollback, engine rollback and automatic rollback of a deliberately broken synthetic `0.0.3`.
- The external recovery runner restored an interrupted installation after its CLI disappeared, without the original update environment variables.
- Every canonical project state file remained byte-for-byte unchanged, and queued work remained queued. No models or remote effects were used by this update acceptance test.
- Release build, Python lint/format, JavaScript checks and package contents verified locally. The hosted CI update matrix is configured but has not been run here.

### Validation of language and documentation changes

- 65 Python tests and six dashboard localization tests passed, including language persistence, project isolation, adapter input, document preservation and focused decision drafts.
- Both languages checked in the real browser at desktop and narrow widths. Selection, unsent answers, expanded details, archive search and reload preferences were preserved.
- Interactive terminal language selection, isolated wheel installation, bundled templates/translations and source distribution contents verified.
- Python lint/format, JavaScript syntax and all nine skill metadata checks passed. Hosted CI is configured but has not been run as part of this change.

### Prior validation

- 2026-09-24: the prior runtime suite passed 57 tests.
- A real terminal exercise covered agent-authored HTML TODOs, selection, parallel implementation, review, landing, triage and issue closure. Two initial tracks recovered in new sessions after the duplicate-search fix; a fresh track then completed without further intervention.
- That disposable public fixture had three merged PRs, three closed issues and 13 passing tests on the final remote main. This does not establish large-codebase, Forgejo or multi-repository support.
