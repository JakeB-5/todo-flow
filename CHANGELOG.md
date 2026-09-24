# Changelog

User-visible behavior, compatibility, important fixes and repository changes. The current development package version is `0.2.0`; these changes have not been published as a separate release.

## Unreleased

### Added

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

- Public skill instructions and starter templates now use English. New user-facing content follows the selected project language; existing documents are preserved.
- README prioritizes product outcomes, actual UI, quick start and an agent installation prompt; detailed operation has moved to shared root guides.
- Tracks and runtime records use files as authority; SQLite is disposable.
- Follow-up registration remains separate from selection; completion and issue closure require current cleared triage.
- `docs/` remains local-only and excluded from Git and distributions. Shared usage and contribution guidance does not depend on it.
- Public documents, examples and metric assets omit identifying source-project metadata.

### Fixed

- Triage duplicate search no longer mistakes historical `revisions/` documents for current track IDs.
- Post-landing repair reopens the issue and distinguishes closure receipts for the new delivery cycle.

### Validation of language and documentation changes

- 65 Python tests and six dashboard localization tests passed, including language persistence, project isolation, adapter input, document preservation and focused decision drafts.
- Both languages checked in the real browser at desktop and narrow widths. Selection, unsent answers, expanded details, archive search and reload preferences were preserved.
- Interactive terminal language selection, isolated wheel installation, bundled templates/translations and source distribution contents verified.
- Python lint/format, JavaScript syntax and all nine skill metadata checks passed. Hosted CI is configured but has not been run as part of this change.

### Prior validation

- 2026-09-24: the prior runtime suite passed 57 tests.
- A real terminal exercise covered agent-authored HTML TODOs, selection, parallel implementation, review, landing, triage and issue closure. Two initial tracks recovered in new sessions after the duplicate-search fix; a fresh track then completed without further intervention.
- That disposable public fixture had three merged PRs, three closed issues and 13 passing tests on the final remote main. This does not establish large-codebase, Forgejo or multi-repository support.
