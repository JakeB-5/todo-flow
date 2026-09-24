---
name: trackrun
description: Execute explicitly selected TODO track IDs with headless workers, durable filesystem handoffs and separate worktrees. Use for trackrun id1 id2 requests after dashboard or track-picks selection.
---

# trackrun — run selected tracks

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for reports unless the user requests another; default to English. Preserve identifiers and commands.

`trackrun ID1 ID2` is the execution entrypoint. State defaults to `todo/` in the current project; use `--state /absolute/STATE` for a different location. During source development, use `uv run trackrun ...`.

Read selected tracks' documents and `state.json`, and the project configuration. Respect the user's selected scope, configured verification and previously granted landing authority. Do not ask for the same authorization again. Recommendations alone do not authorize execution.

The command persists requests, starts a driver and processes currently runnable work. Each assessment, implementation, review and triage uses a fresh headless worker; outcomes and follow-up intent live in files. It exits when no work can currently run, including decision waits. The default concurrency is two; `--jobs N` is optional. There is no resident supervisor agent.

If a separate `todo-flow --state STATE run --daemon` driver already runs, `--request-only` can submit without starting another. Reaching `--max-tasks` preserves pending work; continue with `todo-flow --state STATE run`. A task limit is not a completion verdict.

Check `tasks/` and `attempt-records/` for execution, `results/` for outcomes, `decisions/` for questions and `effects/` for external receipts. No SQL query is required. Unknown file state is not success.

Use `todo-flow --state STATE pause|resume|cancel ID` when requested. Record decisions with `answer DECISION_ID --text TEXT` and restart a driver if needed. Preserve requests, handoffs, receipts and failure history; distinguish recovery with intervention from an uninterrupted run.

For authorized landing, [track-triage](../track-triage/SKILL.md) automatically assesses residue, findings and related watches. Original-scope defects return to work, review and landing on a fresh repair branch. Separate follow-ups become HTML TODO documents awaiting selection. A current cleared triage receipt is required before issue closure and completion. Questions resume the same role; independent tracks can continue.
