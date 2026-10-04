# Operating TODO Flow

[English](OPERATIONS.md) · [한국어](OPERATIONS.ko.md) · [日本語](OPERATIONS.ja.md) · [简体中文](OPERATIONS.zh-CN.md)

[Start here](README.md#quick-start) · [Agent installation](AGENT_INSTALL.md) · [Updates](UPDATES.md) · [Demo](DEMO.md)

## Project state and language

Install the engine once. Initialize each target repository separately, with its own state, skill installation, worktrees and dashboard/driver processes. Use distinct dashboard ports for projects open at the same time.

```sh
# Run from the target repository, using ./todo by default.
trackrun TRACK_ID_1 TRACK_ID_2
todo-flow serve --port 8765

# Explicit state from any working directory.
trackrun --state /absolute/project/todo TRACK_ID_1
```

`init --language en|ko|ja|zh-CN` persists the primary language in `config/1.json`. Interactive setup prompts when the flag is omitted; unattended setup defaults to English. Agent-assisted setup asks the user if no preference or existing configuration is available. Older configurations without the field default to English.

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

`--jobs N` limits concurrent tasks in one driver (default 2), not selected tracks. `--max-tasks N` limits assignments in one run (default 100). Pending work remains in files when that limit is reached. Terminals have no separate count limit: old `terminal_concurrency`, `terminal_idle_limit` and capacity ledgers no longer participate in launch admission. Cleanup inspects only the current execution; an old or unconfirmed UI tab does not block other workers.

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

Codex uses read-only shell tools (including `rg`); Claude exposes Read, Glob and Grep. `context_patterns` / `--context` are navigation hints, not read-access controls. Run with the access appropriate to your project. Workers return JSON proposals; the engine still applies authorized writes, runs verification, commits and handles remote effects. On supported Orca routes, workers use visible sessions bound to their managed worktrees. Compatibility command workers show live logs. Both return proposals through the host; a visible terminal does not authorize extra writes or prompts.

Claude's Orca route starts the CLI with an actual terminal, a fresh `--session-id`, `--safe-mode`, `--restricted`, and Read/Glob/Grep-only tools. It does not use `-p` or `--no-session-persistence`. The existing Claude login is reused; credentials are not read or copied. The host reads only the matching session transcript under the active Claude configuration directory. The exact initial prompt, session/workspace identity, tool results and completed-turn record must match before a proposal is accepted. `claude-session.json` and the copied `claude-transcript.jsonl` preserve that evidence, independently of `terminal-process.json` and `terminal-retirement.json`. The process group is stopped after collection, on cancellation, or on driver loss. Independent review gets another fresh session, even with the same provider/model.

Headless is an explicit override or a fallback whose pre-launch reason is recorded in `launch.json`. Missing managed ownership or unsupported Claude isolation flags are compatibility reasons; errors after a session launch are not. An initial prompt absent from the transcript after 60 seconds produces a startup diagnostic (check trust/authentication in the terminal), not a second launch. Once the turn is accepted there is no additional wall-clock limit beyond `worker_timeout`. Unknown transcript formats, changed candidates and extra prompts stop adoption. Preserve the interactive task intent and reconcile the original attempt; never remove it just to resend a task. Terminal content and sidebar state alone cannot establish completion.

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

**Execution boundaries in `0.0.4`:** ordinary proposal commits include only proposed paths. Unrelated staged and unstaged files remain untouched; existing edits on a proposed path stop application. Remaining checkout changes block verification and review rather than being silently adopted. Merge repairs have a recorded checkout/index checkpoint; edits after preparation, or a resumed merge without a checkpoint, require inspection. Recovery accepts a committed repair only when its parents and recorded tree match.

Verification checks checkout cleanliness before using a cache and checks both HEAD and cleanliness after the command. Review checks the expected HEAD and clean checkout before and after the worker reads it, and rechecks before recording a verdict. Publication and landing also reject a dirty or mismatched candidate. Preserve manual recovery edits and resolve the decision before resuming; do not reset or automatically stage them.

Verification commands run in their own process group. Timeout cleanup signals the entire group, escalates to a kill, reaps the direct process and confirms no live group members remain before returning a failed verification. Children left by a successful parent are also stopped and cause failure. If termination cannot be confirmed, execution stops for attention.

`verify_timeout` accepts positive finite numbers of seconds or JSON `null` for no execution time limit. Omitting it retains the 180-second default. A null timeout is preserved in verification identity and differs from every numeric timeout, so changing between them invalidates cached verification. Claim cancellation, driver-disconnection cleanup, bounded cleanup waits and durable termination confirmation remain active without an execution time limit. `worker_timeout` controls worker execution separately.

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

### Native Orca worker sessions

Workers run without a wall-clock limit by default, including the process supervisor and native proposal transport. An explicit `worker_timeout` in project configuration still applies; use JSON `null` for no limit. Startup handshakes and individual CLI calls remain bounded. The runtime continues checking the claim while a native worker runs, so cancellation stops the owned process group and retires its unchanged viewer even without a time limit. Driver disconnection retains the existing supervisor cleanup barrier.

The visible client runs in a dedicated terminal in the managed worktree. Its host-side bridge uses Orca's installed Codex status hook with the terminal's own routing environment to register the same session in the sidebar. The model's hooks remain disabled. Only a journal matching the task/generation, candidate HEAD, workspace, thread and turn can drive this display. `native-sidebar.json` retains a fresh public `worktree ps` observation of that exact pane; a successful hook process or terminal-create response alone is not confirmation. `launch-status` distinguishes confirmed sidebar sessions from unconfirmed clients. The bridge exits with its client rather than leaving a shell. Sidebar state describes the worker's activity, never verification, review, landing or track completion.

Native failures record their exception type and message in `native-session.json` and report those paths in the decision. Completed proposals and failed/stopped workers project an end-of-turn event before the unchanged viewer is retired. Unknown sidebar delivery preserves an explicit unconfirmed receipt; it cannot weaken process cleanup or proposal validation.

Streaming fragments do not trigger filesystem scans or Git subprocesses. The host checks the current claim and exact HEAD when a complete proposal is available, while the owning driver continues checking cancellation. Every five seconds the collector may read full turn history on the same connection, without starting or resuming another turn. A unique completed final answer for the exact bound thread/turn can supply completion when its notification is missing; partial, ambiguous, failed or mismatched history cannot. One-second receive polls preserve partial WebSocket frames and do not impose a worker deadline. Existing Python worker processes retain their loaded code after a source update; preserve their results and reconcile their owned cleanup before handing work to an updated process.

New Codex tracks can use Orca-managed workspaces, with a durable create intent and independently checked Git/Orca ownership before registration. Existing candidates retain their registered paths. The native adapter uses the existing `CODEX_HOME` and lets Codex handle its configured authentication, including file, keyring and environment credentials. It does not inspect or copy credentials and does not gate execution on a Codex version string. Per-process configuration overrides keep the workspace untrusted, external integrations disabled, the sandbox read-only/no-network and approval policy `never`. Before starting a thread, the adapter reads effective configuration in memory and disables each inherited MCP registration; configuration responses are not written to attempt evidence. Actual startup, authentication and protocol failures remain failures with preserved evidence, not permission to launch a second worker. Codex retains ownership of its login storage and token refreshes.

The host sends each initialize/thread/turn request once and records it before transmission. An uncertain start response blocks replacement attempts for that task; it is never covered by an exec fallback. When a known turn loses its connection but the original server and socket identity remain live, the adapter reconnects once and reads full history for that exact thread/turn. It never repeats thread/start or turn/start, and unknown, additional or incomplete history remains blocked. A visible client receives only the exact remote thread/socket, while the complete final proposal comes from ordered App Server notifications. Terminal acceptance is not worker completion. The host verifies claim and candidate HEAD, and the existing supervisor confirms process-group exit before effects. Viewer identity, activity, exit and complete inventory are checked before retirement. Inspection-to-close input races use the existing authorized policy. Unknown terminal cleanup preserves the tab and its evidence without invalidating a completed proposal. Actual process-group termination and exact session/HEAD checks remain required.

Review uses a new server/thread and known implementation-session provenance; legacy implementation records keep the existing fresh review route. `launch-status` and task inspection expose the actual mode, compatibility reason and resource associations without opening an execution endpoint. Real-model/external live acceptance remains unperformed; local synthetic server/CLI/process fixtures and recorded local protocol probes are separate evidence.
