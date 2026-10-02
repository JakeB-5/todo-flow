# Choose workers for one execution request

A track recommends a plan; the user’s execution mode constrains it. Do not infer pricing, account access or quality equivalence from model names or matching effort labels. Reassess on the day of selection using the actual requirement, uncertainty, risk and the user’s token constraints. No background evaluator or token-balance collector is required.

## Role choices

| Needed role | Starting point | Raise effort or model capability when | Reduce when |
| --- | --- | --- | --- |
| assess | Available Codex or Claude model, low/medium | Evidence is contradictory or architecture is unresolved | Scope and reproduction are already clear |
| work | Available model, medium | Multiple callers, persistence, concurrency or security invariants change | The edit is mechanical and verified locally |
| review | Fresh session, medium/high | Failure would be costly or verification misses important behavior | A small deterministic change has direct evidence |
| triage | Available model, low/medium | Scope or duplicate ownership is ambiguous | Sources have clear dispositions |
| watch | Only when needed, low/medium | A conditional observation needs causal investigation | The trigger can be checked directly |

These are selection heuristics, not benchmarks. A fresh independent review can use the same provider/model as implementation. Never inherit implementation effort merely by accident.

Record only needed roles in `workerPlan.roles`, each with `provider` (`codex` or `claude`), `model`, `effort` and a nonempty `basis`. Use `workerPlan.version: 1`. Model and effort may be null, explicitly accepting the provider default; that is not a claim about which model ran. Top-level document `effort` estimates human/work scope and has no routing effect. `routingAdvice` remains advisory legacy metadata.

## Supported snapshot and its limits

Inspected locally on **2026-10-02**, without network or live model calls:

- Codex CLI model catalog `~/.codex/models_cache.json`, `client_version: 0.159.2`, fetched `2026-10-02T05:40:30.543085Z`: `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-reserve`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` and `codex-auto-review` advertise low/medium/high/xhigh/max. `gpt-5.5` advertises low/medium/high/xhigh. Some advertise ultra with automatic delegation; this read-only worker disables delegation, so ultra is rejected.
- Claude Code **2.1.287**, locally installed `~/.local/share/claude/versions/2.1.287`: embedded model records’ `runtime.effort_levels` list low/medium/high/max for `claude-sonnet-4-6` and `claude-opus-4-6`; low/medium/high/xhigh/max for `claude-sonnet-5`, `claude-sonnet-5-5`, `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5`, `claude-opus-5-5`, `claude-fable-5` and `claude-fable-5-1`. The embedded command definition accepts `--effort <level>`.

These sources establish local client support, not universal availability, entitlement, price or successful inference. Managed provider settings may cap effort. Check your installed client/account before using these examples. Unknown model/effort pairs stop before launch; use a supported pair or explicitly set effort to null for provider defaults. Do not silently drop an unsupported explicit effort.

Codex command launches pass `--model` and `-c model_reasoning_effort="..."`. Native Codex passes the model and reasoning config to thread/start and model/effort to turn/start. Claude command launches pass `--model` and `--effort`. Existing read-only tools, fencing, fresh sessions, cleanup and uncertain-launch barriers still apply.

## Three modes and overrides

The resolver first enforces mode, then considers explicit request roles, registered roles, the project worker, and allowed project profiles. A disallowed candidate is skipped with its reason recorded. An allowed but unsupported explicit combination is an error, not permission to downgrade it. No suitable candidate opens a configuration decision before provider launch.

For a mixed registered plan and a Codex project default:

```sh
trackrun TRACK_ID --worker-mode auto
trackrun TRACK_ID --worker-mode codex-only
```

Auto permits either provider; it does not force both. Codex-only uses allowed registered roles or the Codex project default when a Claude recommendation conflicts. To run Claude-only without a Claude project default, save the following as `claude-roles.json` (omit watch unless used):

```json
{
  "assess": {"provider": "claude", "model": "claude-sonnet-4-6", "effort": "low", "basis": "Clear scope; conserve today's Codex allowance"},
  "work": {"provider": "claude", "model": "claude-sonnet-4-6", "effort": "medium", "basis": "Known implementation path"},
  "review": {"provider": "claude", "model": "claude-opus-4-6", "effort": "high", "basis": "Fresh independent examination of changed invariants"},
  "triage": {"provider": "claude", "model": "claude-sonnet-4-6", "effort": "medium", "basis": "Review landed evidence and overlap"},
  "watch": {"provider": "claude", "model": "claude-sonnet-4-6", "effort": "low", "basis": "Check an explicitly required observation"}
}
```

```sh
trackrun TRACK_ID --worker-mode claude-only --worker-roles claude-roles.json --request-only
todo-flow run
```

Use `--state STATE` with both commands when state is elsewhere. `todo-flow start` accepts the same routing options. A configured project may supply `worker_profiles: {"codex": {"model": "gpt-6.1-sol", "effort": "medium"}, "claude": {"model": "claude-sonnet-4-6", "effort": "medium"}}` as allowed alternatives. A custom command is supported in auto but cannot establish an exclusive provider boundary.

## Persistence, reselection and evidence

Canonical `execution.routing` events freeze the request mode, explicit roles, document plan and project defaults. They are keyed by track and request ID, so simultaneous tracks may use different modes. Claim records (`worker.claimed` events) freeze each attempt’s selection, source, basis and skipped candidates. Existing event storage commits these with the request/claim; no new state table is required.

Omitting options on repeated start/trackrun preserves the snapshot. Passing a new mode or role file explicitly updates only future attempts; a role file replaces the override map, and `{}` clears overrides. Pause/resume, new drivers, retry and post-landing repair preserve the same request constraint. Reselection does not cancel a running attempt, rewrite its receipt, or authorize replay of an uncertain launch. Resolve an existing configuration decision through the normal answer/resume path after correcting the selection.

Tracks with no plan, explicit routing options or worker profiles keep the legacy single/custom-worker path. Old requests adopt routing only when explicitly selected or when they have an authored plan/profile.

`attempts/ATTEMPT/worker-selection.json` separates `selected` from `provider_confirmed`. Command execution currently leaves confirmation null. Native `native-spec.json` and `native-session.json` retain requested settings and provider-returned model/reasoning effort separately. A missing response stays null; selection or terminal acceptance never proves the actual model, successful inference or completion.
