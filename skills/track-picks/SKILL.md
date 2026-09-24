---
name: track-picks
description: Read filesystem TODO tracks and current execution facts to propose work that can start now, needs a decision, or is blocked. Use for trackpicks and candidate selection; never starts workers by itself.
---

# track-picks — select candidates from durable facts

Read installed `project.json` for STATE and the primary language, then STATE's `config/1.json`. Report in that language unless the user requests another; default to English. Preserve identifiers and commands.

The default state is the project's `todo/`. Selection is read-only; execution is a separate `trackrun ID…` request.

1. Narrow current records with `rg -n '"status"|"control"|"request"' STATE/tracks -g state.json`. Read candidate `track.html` (or legacy `track.md`) goals, priorities, triggers and dependencies. Start in the requested area while checking relevant external prerequisites. Avoid dumping every file.
2. Find related IDs in `tasks`, `decisions`, `watches`, and, when needed, `events`. Show already active or paused tracks as current activity, not new execution candidates. Request acceptance is different from a worker running. Stale ownership, leases or observations do not prove current activity.
3. Check a prerequisite's current state and required output rather than repeating an old “after X” note. Distinguish a required code/API dependency from a decision relationship or a final external check; explain what can proceed now. Do not defer required conditions to Watch to make a track appear completable.
4. Shared files, areas or consumer packages indicate integration risk, suggested landing order and combined verification needs. They do not automatically prohibit parallel implementation. Name an actual prerequisite or shared writable resource when it blocks work.

Put the track ID first and group candidates as **ready now / needs a user decision or input / blocked or unverified**. Briefly state priority, area, evidence and available scope. List recommended parallel and subsequent work by concrete ID.

Finish with `trackrun ID1 ID2` for the recommended set. Do not emit an empty command. Do not edit documents, state or decisions, or spawn workers during selection. If the same user request explicitly authorizes execution, continue through the trackrun skill with that selected scope.
