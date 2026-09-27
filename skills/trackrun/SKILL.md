---
name: trackrun
description: Execute explicitly selected TODO track IDs with replaceable workers, durable filesystem handoffs and separate worktrees. Prefer visible terminals when available. Use for trackrun id1 id2 requests after dashboard or track-picks selection.
---

# trackrun — run selected tracks

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for reports unless the user requests another; default to English. Preserve identifiers and commands.

`trackrun ID1 ID2` is the execution entrypoint. State defaults to `todo/` in the current project; use `--state /absolute/STATE` for a different location. During source development, use `uv run trackrun ...`.

Read selected tracks' documents and `state.json`, and the project configuration. Respect the user's selected scope, configured verification and previously granted landing authority. Do not ask for the same authorization again. Recommendations alone do not authorize execution.

Keep follow-up work within the selected goal and conditions, interpreted with the user's recorded decisions. New discoveries do not authorize additional features. A required correction must address that outcome or an existing invariant affected by the change; optional improvements remain findings. Reconcile a necessary scope change explicitly instead of expanding the agenda under the same track ID.

The command persists requests, starts a driver and processes currently runnable work. Each assessment, implementation, review and triage uses a fresh worker with a workspace and evidence paths; the worker searches and reads what it needs. Outcomes and follow-up intent live in files. It exits when no work can currently run, including decision waits. The default task concurrency is two; `--jobs N` is optional. There is no separate terminal-count limit or historical-terminal admission audit. Preserve old launch records and ledgers as evidence; do not require migration or cleanup of those records before starting another worker. There is no resident supervisor agent.

Default launcher `auto` prefers an available Orca project terminal, then a configured terminal command or an existing tmux session, and falls back to headless when none is available. Compatibility terminals show automatic command-worker progress. On the supported native route, a Codex terminal client observes the exact host-owned read-only session in its managed workspace. The host submits the task and collects the complete proposal; do not manually resend a prompt or reuse that client for another task. Use `--launcher headless` only when requested or appropriate for the environment; `--launcher orca` requires Orca. Inspect `attempts/ATTEMPT/launch.json` for the actual backend and terminal handle. An accepted terminal launch is not proof of worker execution or completion; compatibility terminal workers record their PID and exit in `terminal-process.json`. Native attempts additionally preserve `native-session.json`, per-request receipts and the supervising process inventory; terminal acceptance alone still does not establish work completion. An uncertain launch must not be retried as a second headless worker. Native reconnect reads the same live owned server/thread/turn history without resubmitting input; unknown server or history identity remains blocked. Native workers reuse the existing Codex login without checking a version allowlist or credential-file shape. Actual startup/authentication/protocol errors remain visible and never trigger a duplicate launch. Explicit headless and legacy candidates retain an explained compatibility route.

If a separate `todo-flow --state STATE run --daemon` driver already runs, `--request-only` can submit without starting another. Reaching `--max-tasks` preserves pending work; continue with `todo-flow --state STATE run`. A task limit is not a completion verdict.

After completion, the runtime cleans owned, clean and remotely included worktrees plus confirmed exited worker terminals. Documents, branches, results and attempt logs remain. Use `--no-auto-cleanup` only when resources need to remain for inspection. Inspect `cleanup/` and cleanup events for deferred resources; `cleanup TRACK_ID --dry-run` explains the plan, and `cleanup TRACK_ID` retries. Cleanup failure does not mean the delivered track needs to run again. Never force-remove user changes or a reused terminal.

Check `tasks/` and `attempt-records/` for execution, `results/` for outcomes, `decisions/` for questions and `effects/` for external receipts. No SQL query is required. Unknown file state is not success.

Use `todo-flow --state STATE pause|resume|cancel ID` when requested. Record decisions with `answer DECISION_ID --text TEXT` and restart a driver if needed. Preserve requests, handoffs, receipts and failure history; distinguish recovery with intervention from an uninterrupted run.

For authorized landing, [track-triage](../track-triage/SKILL.md) automatically assesses residue, findings and related watches. Original-scope defects return to work, review and landing on a fresh repair branch. Separate follow-ups become HTML TODO documents awaiting selection. A current cleared triage receipt is required before issue closure and completion. Questions resume the same role; independent tracks can continue.
