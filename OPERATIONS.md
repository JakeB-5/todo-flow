# Operating TODO Flow

[Start here](README.md#quick-start) · [Agent installation](AGENT_INSTALL.md) · [한국어](README.ko.md)

## Project state and language

Install the engine once. Initialize each target repository separately, with its own state, skill installation, worktrees and dashboard/driver processes. Use distinct dashboard ports for projects open at the same time.

```sh
# Run from the target repository, using ./todo by default.
trackrun TRACK_ID_1 TRACK_ID_2
todo-flow serve --port 8765

# Explicit state from any working directory.
trackrun --state /absolute/project/todo TRACK_ID_1
```

`init --language en|ko` persists the primary language in `config/1.json`. Interactive setup prompts when the flag is omitted; unattended setup defaults to English. Agent-assisted setup asks the user if no preference or existing configuration is available. Older configurations without the field default to English.

Skills are written in English and use the primary language for new documents and user-facing explanations. Installed `project.json` identifies their project state and language. Built-in worker adapters receive the same language instruction; custom adapters receive `language` and `output_language_instruction` in their input. Schema keys, IDs, commands, code conventions and original source quotations stay unchanged.

The dashboard switch stores a browser-local preference keyed by project. It preserves selections, search, decision drafts and the current view. It does not rewrite project configuration or translate authored documents, logs or historical results. Existing execution configuration is immutable; do not edit it during a run.

## Documents and files

[HTML starter](templates/track.html) · [Markdown starter](templates/track.md) · [JSON starter](templates/track.json)

```sh
todo-flow --state STATE register /absolute/scratch/track.html \
  --assets /absolute/scratch/track-assets

# Revise a document after reading its current revision.
todo-flow --state STATE register /absolute/scratch/revised.html --expected-revision 2
```

HTML preserves the full body and SVG, image, CSS, JavaScript and Three.js resources. Markdown preserves its full source and renders to HTML. Use `assets/...` paths; each revision snapshots its own resources. The HTML execution contract is an `application/json` block with ID `todo-flow-track`.

Set the contract's `language` and HTML `lang` to the selected authoring language. JSON registration defaults to the project language when omitted. Markdown rendering uses its metadata language and defaults to English; authored HTML is served without rewriting it. Required goal/scope/evidence/condition fields must agree with the visible document.

```text
todo/
  config/1.json                    Project execution contract and language
  tracks/<id>/track.html            Human-reviewable document
  tracks/<id>/source.md             Original Markdown, when supplied
  tracks/<id>/assets/               Current document resources
  tracks/<id>/state.json            State, request and execution references
  tracks/<id>/revisions/            Historical documents and resources
  tasks/ · attempt-records/         Work and worker attempts
  results/ · decisions/ · events/   Outcomes, questions and event history
  effects/                         External intent and confirmation receipts
  findings/ · triages/ · watches/   Findings and their dispositions
  attempts/                        Raw worker input, output and diagnostics
  .cache/query.sqlite              Disposable query cache
```

The filesystem is authoritative. Use `rg`; SQL is not required. Cache deletion does not delete the canonical work. Do not overwrite registered documents directly to bypass revisions or reuse stale evidence.

The dashboard opens `/documents/ID/REVISION/index.html`. Use HTTP rather than `file://` for local JavaScript modules and simulations. Documents are sandboxed separately from the dashboard. Inspect actual rendering and interactions before claiming visual review.

## Selection, execution and concurrency

- Register and revise through the todo skill/CLI. The dashboard has no authoring controls.
- Select through the dashboard or track-picks. The dashboard copies a `trackrun` command; it does not send an execution request.
- Execute concrete IDs with trackrun. Different tracks use separate worktrees. Workers choose bounded useful next work from durable context.
- Active TODOs use a continuous list without page buttons. Completed tracks have a separate searchable archive with incremental loading.

`--jobs N` limits concurrent tasks in one driver (default 2), not selected tracks. `--max-tasks N` limits assignments in one run (default 100). Pending work remains in files when that limit is reached.

```sh
# Optional: submit to an already running driver.
trackrun --state STATE TRACK_ID --request-only

# Optional: wait for new work. The assignment limit still applies.
todo-flow --state STATE run --daemon
```

Multiple drivers do not share a global concurrency budget. A dead driver or expired claim is not completion. Review owners, leases, attempts and results before inferring current activity.

<a id="worker-context-and-terminal-launchers-unreleased"></a>

## Worker context and terminal launchers

This section describes `0.0.2`; the published `0.0.1` wheel retains snapshot workers. Workers now start in the assigned implementation/review checkout, or the exact fetched-base checkout for triage. Input contains the task, workspace, head, language, exploration hints, write boundaries and a `paths` map. Goal/conditions, the rich track document, full diff, verification, decisions, prior results and triage evidence are read by path. Project source is not collected into stdin, and there is no aggregate 150 KB source limit. Model context limits still apply to selected reads.

Codex uses read-only shell tools (including `rg`); Claude exposes Read, Glob and Grep. `context_patterns` / `--context` are navigation hints, not read-access controls. Run with the access appropriate to your project. Workers return JSON proposals; the engine still applies authorized writes, runs verification, commits and handles remote effects. These are automatic workers with live logs, not interactive agent chats.

`init --launcher auto` is the default, including existing configurations without `worker_launcher`. Each driver can override it without changing project configuration:

```sh
trackrun TRACK_ID --launcher auto
trackrun TRACK_ID --launcher orca
todo-flow --state STATE run --launcher headless
```

`auto` first uses a running Orca runtime that recognizes the project's repository. It opens a titled terminal under that project and starts the worker in its assigned checkout. Otherwise it uses a configured `terminal_command`, then tmux when invoked inside an existing tmux session, then headless. Explicit `orca`, `tmux` or `terminal` modes fail when unavailable. Remote Orca PTYs require a driver on that host; they are not launched from a local driver. Exited worker terminals are closed after track completion when their identity and inactivity can still be verified; their logs remain in the attempt directory. Use `--no-auto-cleanup` when completed run resources should remain open for inspection.

For another terminal application, configure `terminal_command` as an argv array for a trusted launcher that returns after opening the terminal. `{command}` is the shell-quoted worker bridge command; `{cwd}` and `{title}` are optional placeholders. The launcher must start that command unchanged and return within ten seconds. Authentication is inherited from the terminal environment; credentials are not copied into launch records. Use headless if the required authentication exists only in the calling shell.

Each attempt preserves `launch.json` (backend and Orca terminal handle or launcher receipt), `terminal-process.json` (actual worker PID and exit code), `input.json`, `output.json` and `stderr.log`. Opening a terminal does not prove that a worker started or completed. Completion requires a recorded exit and a valid result. An ambiguous create/start failure does not launch a second headless worker; the attempt records cancellation for delayed starts. Timeout stops the worker process group. Inspect the attempt before retrying.

The path-based input is worker protocol **2**; result proposals retain their existing schema. Existing built-in Claude/Codex configurations are adapted without editing project state. Legacy custom command adapters must read `workspace` and `paths`, then explicitly set `worker_protocol: 2` while the project is stopped. They fail before spawning until migrated, rather than silently receiving a different contract.

## Questions, interruption and recovery

```sh
todo-flow --state STATE status
todo-flow --state STATE pause TRACK_ID
todo-flow --state STATE resume TRACK_ID
todo-flow --state STATE cancel TRACK_ID
todo-flow --state STATE answer DECISION_ID --text 'The decision and its reasoning'
todo-flow --state STATE run
```

Restart against the same state after interruption. Late responses from superseded claims are rejected. Remote effects are reconciled with durable receipts to avoid accidental duplication after a lost response. `resume` or `answer` may need a driver restart if none is running.

Diagnose tool failures from attempt records rather than replacing them with invented decisions. If a port is occupied, use a different port. If installed commands are not found, inspect `uv tool dir --bin` and PATH. If a skill is not discovered in the current session, read its installed file directly or start a new session.

## Review, landing and completion

Review uses a fresh agent context independent of implementation and examines the exact candidate and verification. When the same GitHub account owns the PR, the assessment is a COMMENT review, not another person's APPROVE.

The default endpoint is `review`. An explicitly authorized `land` endpoint requires both `--endpoint land` and `--allow-land` at initialization. The host combines current base and candidate in an isolated checkout, verifies that combined tree, and publishes the exact verified merge. Base advancement triggers another comparison; branch protection is not bypassed.

**Integration repair in `0.0.3`:** an actual merge conflict or failed combined verification records durable repair intent and invalidates the old review/verification. Before the work session, the host fetches and pins the current base, then merges it into the owned candidate checkout. `paths.integration_repair` describes the pinned commits, failure, base-diff path and conflicting paths; each conflict includes readable ancestor/candidate/base versions, and workspace files contain the merge markers. Content remains in files rather than being injected into the prompt. Git failures without unmerged paths are reported as execution errors.

The worker returns resolved UTF-8 file proposals or a concrete question. Dirty checkouts are preserved, missing resolutions and remaining markers block commit, and unsupported binary/deletion resolutions require attention. The host records both merge parents, verifies and publishes the repaired candidate, then requests a fresh independent review before retrying landing. Decision answers and interrupted attempts resume the recorded merge without aborting it; a later base advance is checked again at landing.

Confirmed landing schedules triage. A current cleared receipt is required before issue closure and completion. Original obligations cannot be moved to a follow-up TODO or Watch to make the source track pass. Repairs use a fresh branch, verification and independent review. Separate new TODOs are registered but await human review and selection.

## Watch and optional Jev

```sh
todo-flow --state STATE watches
todo-flow --state STATE signal TRIGGER --version VERSION
todo-flow --state STATE watch-dispose WATCH_ID --status resolved --evidence 'Current evidence'
# Promotion additionally requires --target TRACK_ID.
```

An inactive track's signal does not start new code execution. Confirmed defects need work or a selected track; watches represent conditional observations with a deferral reason, trigger and next action. Closure requires evidence, not elapsed time.

The todo and watchlist skills recommend **Jev as an optional screening aid**: investigation sufficiency and overlap during registration; changed-source relevance and finding prioritization during watch review. Begin with available files and tools. An absent or failed Jev integration does not block the current request. Use a configured integration's documented interface, retain your reasoning, and do not treat scores as proof of resolution. No Jev client, credentials or remote calls are installed by TODO Flow.

## Updates and compatibility

Use [UPDATES.md](UPDATES.md) for guarded engine replacement, manifest-based skill updates, rollback and recovery. The runtime rejects unsupported state/config formats before using them. Package version changes do not automatically rewrite project data. Engine updates require idle cooperative processes across known projects; skill updates only require the relevant project to be idle. Existing requests and documents remain in place.

## Cleanup, migration and hooks

```sh
todo-flow --state STATE cleanup TRACK_ID --dry-run
todo-flow --state STATE cleanup TRACK_ID
todo-flow migrate-files --source OLD_SQL_STATE --target NEW_FILE_STATE
todo-flow --state STATE hooks
```

In `0.0.2`, completion requests automatic cleanup of the track's implementation worktrees (including earlier repair attempts), integration checkouts, triage checkouts and exited worker terminals. The published `0.0.1` release only has explicit cleanup of the current implementation worktree. `init --no-auto-cleanup` disables automatic cleanup for a new project; `trackrun ... --no-auto-cleanup` or `run --no-auto-cleanup` disables it for one driver. Manual cleanup remains available.

Cleanup preserves the main checkout, local branches, track documents, revisions, results, verification/review/triage evidence and raw attempt logs. It checks the actual checkout HEAD against the fetched remote base, not just the recorded candidate SHA. Unlanded candidates, unfinished work, dirty/untracked files, unknown ignored files, changed terminal identities and terminals with newer activity are retained with a reason. Ignored Python `__pycache__/*.pyc` files are disposable; other ignored files require inspection. No forced worktree removal or branch deletion is used.

Orca terminal closure uses the recorded PTY/incarnation and a fresh inventory. A reused terminal is preserved. tmux windows are closed only when the named single pane has exited; custom terminal launchers without a supported close interface require manual closure. An unresolved terminal also retains its associated checkout.

Cleanup intent and per-resource outcomes live in `cleanup/TRACK/EXECUTION.json` and `cleanup.requested`, `cleanup.complete` or `cleanup.deferred` events. An interruption can be retried with `cleanup TRACK_ID`; a subsequent driver retries pending cleanup requests. A cleanup problem does not reopen delivered work or rerun agents. Historical workspace paths remain in evidence after removal; the retained Git branches/commits preserve the source.

Migration copies the legacy SQL store into new file state; it is not an importer for another tool's ledger.

Internal hooks are durable events, including document registration, execution acceptance, worker claims, work results, verification, effect confirmation, decision answers, controls, claim recovery, watch changes, triage and completion. A filesystem redo journal and durable agenda preserve handoffs. External callback hooks are not currently provided.
