# TODO Flow

**Pick the work. Let agents carry it forward.**

Turn selected TODOs into parallel work, independent reviews and verified delivery—with files that survive the session and a dashboard that shows who is doing what.

**English** · [한국어](README.ko.md)

[Get started](#quick-start) · [See it in action](#see-it-in-action) · [For agents](#for-agents) · [Operations](OPERATIONS.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-257854)](LICENSE) [![Status: development](https://img.shields.io/badge/status-development-d4a34b)](#current-scope) [![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab)](pyproject.toml)

## TODO Flow in 37 seconds

https://github.com/user-attachments/assets/8b5acf19-292a-4205-aef1-f48b75305327

*Animated subway-map overview: track selection, parallel agents, session handoffs, verification, review and landing.*

## Development activity, up to 33×.

![From TODO to delivered. 33× daily commit activity.](assets/metrics/workflow-impact.png)

**Register. Pick. Run.** You set the direction; replaceable agents carry out investigation, implementation, verification and review, then landing and triage when authorized.

<sub>Anonymous predecessor workflow: daily commits increased from 9.3 in January to 316.3 in September 2026 (September 1–23). Commit activity, not a measured multiplier of labor productivity or a benchmark of this package.</sub>

<details>
<summary>January–September growth and measurement notes</summary>

![Daily development activity from January to September](assets/metrics/workflow-growth.png)

Adjusted source changes per day increased **4.52× from June to September**; recorded completion transitions per day increased **2.30× from July to September**. Different metrics describe different outcomes: January–September source changes rose 1.09×, and September completion transitions were lower than August.

![Monthly source additions, deletions and exclusions](assets/metrics/source-changes.png)

[Method and monthly figures](assets/metrics/README.md) · [Aggregate JSON](assets/metrics/measurements.json) · [CSV](assets/metrics/monthly.csv). These are observations from predecessor operations, not a controlled causal experiment. Identifying source material is not published.

</details>

## The workflow cycle

![TODO Flow cycle: todo registers plans, trackpicks recommends tracks for your selection, trackrun executes selected IDs, and watchlist reassesses conditional observations. Actionable follow-ups return to todo.](assets/workflow/cycle-en.svg)

**`todo` → `trackpicks` → `trackrun` → `watchlist` → `todo`.** Register a reviewable plan, choose the work, execute selected tracks, and reassess what needs attention. You can also select tracks directly in the dashboard. Only tracks eligible for a new execution request have enabled checkboxes. Tracks marked **Requested**, **Paused**, or **Pause requested** cannot be selected again. Open a track title to inspect its details; use **Resume** there to continue paused work. `trackpicks` recommends work; it does not start workers.

`trackrun` stops at review by default. With landing authorized, it continues through landing and triage, including reassessment of that track's open watches. Use `watchlist` for explicit reassessment; actionable findings return to an existing or new TODO. New follow-up tracks await your selection. These are entry points into shared project state, not mandatory phases for every track.

## See it in action

**We have started applying TODO Flow to this repository.** See the [actual setup, dashboard capture and reproducible configuration](examples/self-hosting/README.md). The initial snapshot has no registered tracks yet; execution results will be documented as real work runs.

![TODO selection, activity, document review and archive in the running dashboard](assets/demo/dashboard-tour.gif)

*Captured from the real dashboard with explicitly labeled synthetic data. This UI tour does not represent a live model run.*

[Dashboard screenshot](assets/demo/dashboard-en.png) · [한국어 dashboard](assets/demo/dashboard-ko.png) · [Reproduce the demo](DEMO.md) · [Plan preview](assets/demo/track-example.png) · [Example HTML](examples/retry-backoff.html) · [Real issue → merged PR](DEMO.md#inspect-a-previous-public-acceptance-run)

The tour shows a dense, continuous TODO list, selection for `trackrun`, current workers and decision waits, a rich track document, and a separate completed archive. For the full execution path, follow the [two-track walkthrough](DEMO.md#run-the-full-workflow) or run the documented live acceptance test in a disposable project.

## Built for work that outlives a chat

| What you need | What TODO Flow provides |
|---|---|
| Move several tasks forward | Selected tracks run in separate Git worktrees; workers take bounded pieces of work. |
| Recover after a session ends | Documents, claims, results, questions and follow-up intent stay in searchable files. |
| Review the plan before execution | HTML documents preserve diagrams, images, scripts and simulations. Markdown must render to HTML. |
| Know who is doing what | A compact dashboard shows current work, owners, waits and evidence. Completed work has its own archive. |
| Deliver with evidence | Exact-candidate verification, independent agent review, authorized landing and post-landing triage. |

Useful for a backlog of independent changes, work that spans sessions, and reviewing several candidate changes before integration. On the development branch, workers receive workspace and evidence paths, then search and read relevant files themselves. See [current scope](#current-scope) for release availability and remaining boundaries.

## Quick start

Choose **English (`en`) or Korean (`ko`) during project setup**. This sets the project's default dashboard language and the language requested from agents for reports and new track documents. Skill instructions remain English. The dashboard also has an English / 한국어 switch for your personal display preference.

### Let your agent set it up

Paste this into your coding-agent session, filling in the project and first task:

```text
Install TODO Flow in /absolute/my-project following
https://github.com/JakeB-5/todo-flow/blob/main/AGENT_INSTALL.md. Ask me to choose English or Korean if I have not specified it.
My first task is: [the change and expected result].
Register a reviewable HTML TODO and show me its link. Select and run the
track that covers this request, then report the actual result and next steps.
```

[For agents](#for-agents) explains the installation contract. Setup-only requests stop after setup; the prompt above also requests the first run.

### Install manually

Prerequisites: **Python 3.11+, uv, Git, and an authenticated Claude or Codex CLI**. GitHub issues and PRs additionally need authenticated `gh`. Install the published release:

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.9/todo_flow-0.0.9-py3-none-any.whl
todo-flow --version
```

[Release assets and checksums](https://github.com/JakeB-5/todo-flow/releases/tag/v0.0.9). This installs the CLI and bundled dashboard/skills; no checkout is needed. For source development, clone this repository and use `uv sync --frozen` and `uv tool install .`.

In the **target project**, use its real verification command, base branch and relevant file patterns. This example assumes an existing Python project with a test suite, an initial Git commit and an `origin` remote:

```sh
cd /absolute/my-project
todo-flow init --repo . --base main --worker codex \
  --language en \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md

todo-flow install-skills --target .agents/skills
todo-flow serve --port 8765
```

Use `--language ko` for Korean. Omitting it prompts in an interactive terminal and defaults to English without a terminal. For Claude sessions, install to `.claude/skills`. Skills inherit the configured language. Add `--github OWNER/REPOSITORY` to `init` for GitHub issues and PRs.

Open **http://127.0.0.1:8765**. Ask your agent to use the installed todo skill, review the generated document, then select and run its actual ID. Setup is complete when the dashboard opens, the registered document renders and the requested first run reaches its configured endpoint. [Detailed setup and recovery](AGENT_INSTALL.md).

### Coexist with another workflow

Without collisions, all nine skill names and existing calls stay unchanged. Use `.agents/skills` for Codex and `.claude/skills` for Claude. Check `todo-flow install-skills --help` for `--alias`; older releases may need an authorized update or source build containing this feature.

Use aliases only for names actually occupied by another workflow. This example assumes `todo`, `track-picks`, `track-run` and `watchlist` are occupied and the four aliases are unused:

```sh
FLOW_STATE=/absolute/my-project/todo-flow-state
FLOW_SKILLS=/absolute/my-project/.claude/skills
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS" \
  --alias todo=flow-todo --alias track-picks=flow-track-picks \
  --alias track-run=flow-track-run --alias watchlist=flow-watchlist
```

Retain the existing STATE for an initialized TODO Flow project. If another tool owns `todo/`, choose a separate STATE before initialization and pass `--state "$FLOW_STATE"` to the earlier `init` command. Do not initialize over the other tool's state or use `--adopt` to claim its skills. An occupied alias aborts the whole skill installation; choose an unused alias or a separate supported target. Existing files and links are preserved, and unoccupied roles keep their names.

Check the installation report's `entrypoints` (canonical role → installed name), `state` and `language`, plus each installed `project.json`. In this example, ask the agent to use `flow-todo`, `flow-track-picks`, `flow-track-run` or `flow-watchlist`; original occupied names still invoke the other workflow. `trackrun` and the internal roles retain their names, and every installed role table links to the actual TODO Flow entrypoints. Shell commands remain `todo-flow` and `trackrun`; use `trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID` for the separate STATE.

Updates reuse the saved mapping without repeating aliases:

```sh
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --dry-run
# Review the plan and resolve conflicts before applying:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS"
```

Local edits are preserved when the upstream file is unchanged; simultaneous changes stop the whole update. Keep installation manifests. See [coexistence, recovery and rollback](AGENT_INSTALL.md#coexist-with-occupied-skill-names) for `--recover` and `--rollback BACKUP_ID`. The disposable bundle tests check filesystem layout, metadata and links. Actual Claude session discovery and model invocation are separate checks; these tests do not perform them. If a session has not refreshed, open a new session or explicitly read the installed `SKILL.md`.

### Request worker attempt limits

For an explicitly bounded request, use `trackrun TRACK_ID --worker-attempt-limit 10` or `todo-flow --state STATE start TRACK_ID --worker-attempt-limit 10`. The positive limit applies separately to each selected track's request. Omitting it retains the existing unlimited request policy. `--max-tasks` still defaults to 100 and limits tasks handled by one driver invocation; restarting that driver does not reset a request's worker limit.

Assessment, implementation, independent review, triage and watch attempts reserve one attempt atomically at claim time. Failed or interrupted attempts remain charged, including failures before worker launch. Host verification, landing and completion tasks do not consume worker attempts. Files in `budgets/` preserve the limit and cumulative usage; `status`, driver output, decisions and budget events expose usage, candidate HEAD and remaining tasks. A budget stop preserves results and pending obligations and does not mean the goal is complete.

Resume a budget stop with `todo-flow --state STATE answer DECISION_ID --text 'Approve additional attempts' --additional-worker-attempts 5`, then restart `todo-flow --state STATE run` if needed. The positive increment extends the existing total limit without clearing usage or replacing pending work. A plain answer, `pause`/`resume`, or repeating `start`/`trackrun` cannot grant extra attempts. Use the CLI for budget approval; ordinary dashboard answers remain available for other decisions.

### Run optional verification preflight checks

For new state, add this argument to your `init` command to run explicitly selected cheap checks before the existing `--verify` command:

```sh
--verify-preflight '[["uv","run","ruff","check","src","tests"],["uv","run","ruff","format","--check","src","tests"]]'
```

This is an argument, not a standalone command. Install the example tools first or replace these argv arrays with checks available in your project. Commands run in order in the candidate workspace, without an implicit shell, using the verifier environment and `--verify-timeout` for each command. The first failure or timeout stops verification; cancellation stops the supervised process and prevents later commands. All checks must pass before the unchanged full verifier runs. Preflight success alone never grants verification success or landing eligibility.

Omitting the option or using `[]` preserves the existing execution path. Verification records include each attempted check's stage, argv and log reference; an explicit requirement failure reaches repair work, while an inconclusive execution reaches diagnosis. The ordered list participates in verification identity, so changing it invalidates previous success. Declare external check scripts and other relevant inputs using `--verify-identity` as described below. Existing state cannot be reconfigured with `init`; the same configuration migration limitation described below applies.

### Select intermediate verification checks

For new state, add `--verify-related '[["uv","run","python","-m","unittest","tests.test_calc"]]'` to `init`, replacing the example with checks supported by your project. The ordered argv arrays are stored as `verify_related`; there is no automatic impact analysis. As with preflight, existing state cannot be reconfigured using `init`.

Intermediate change proposals run these related checks after configured preflight checks. Each command uses the candidate workspace, captured verifier environment and verification timeout, with the same cancellation and failure handling. A partial success supplies feedback only. A worker's `verify:true`, publication request, or review/land/complete follow-up requires the unchanged full `verify` command. Explicit verification tasks, recovered proposals and integration verification also require full verification. Preflight runs before either selected path; related checks do not replace any part of the configured full verifier.

Omitting `verify_related` or setting it to `[]` preserves full verification after each change. Records bind scope and the ordered related-check policy to verification identity; partial results cannot satisfy a full cache lookup or an effect gate. Policy changes invalidate prior evidence. Legacy full records with matching supported identity remain valid when no related policy is configured; records without identity still require fresh verification. Declare external related-check scripts through `--verify-identity` too. No wall-clock speedup is promised: the local regression fixture compares two intermediate changes and one final candidate, reducing full calls from three to one while still detecting a final defect.

### Verification outcomes

Verification records retain `ok` and add `outcome` (`passed`, `failed`, or `inconclusive`) and `reason`. Only `passed` with `ok:true` can supply success, subject to the existing scope, clean HEAD, input identity, process and independent-review gates. Explicit non-pass, cancellation, incomplete evidence or a non-passing recorded check cannot be overridden by `ok:true`.

A verifier may report a result in its **last stdout line**, using a JSON object followed by the literal suffix ` TODO_FLOW_RESULT_V1`:

```text
{"outcome":"failed","reason":"add(2, 3) returned 6; expected 5"} TODO_FLOW_RESULT_V1
```

The whole line must fit in 4096 UTF-8 bytes; `reason` must be a nonempty string. Normal logs may precede it. A malformed marked line is inconclusive. The verifier is responsible for distinguishing a checked requirement violation (`failed`) from an environment/check failure (`inconclusive`); the host does not infer that distinction from `AssertionError`, installation diagnostics or other exception text. A declared failure may accompany a nonzero exit, but does not make its log receipt complete. A declared pass still requires zero exit, complete output and confirmed normal process termination. Timeouts and leftover processes take precedence over declarations.

Compatibility is explicit: an unmarked command with zero exit retains its previous success behavior under all existing barriers. An unmarked nonzero exit still has `ok:false`, now classified as `inconclusive` because an exit code alone does not prove a requirement violation. Historical boolean records are not rewritten: `ok:true` remains eligible only with matching supported identity and the other existing gates; `ok:false` remains non-passing and is shown/routed as inconclusive unless an explicit outcome establishes a requirement failure. Missing or unknown structured outcomes cannot grant success.

Both explicit verify tasks and verification after proposals route `failed` to requirement repair and `inconclusive` to assessment of the recorded stage, logs and environment. Diagnosis preserves the candidate and requests verification of the same HEAD after authorized recovery, or a concrete decision when recovery is unclear. It does not authorize automatic dependency installation or speculative product edits. Inconclusive integration verification preserves candidate evidence and asks for a recovery decision instead of initiating a product repair. A non-passing result is never cached as success, so environment recovery can be checked without a product commit.

### Declare verification inputs

Version `0.0.5` supports `init --verify-identity`. For **new state**, add an argument like this to your real `init` command, replacing the example paths with existing inputs used by your verifier:

```sh
--verify-identity '{"version":1,"files":["/absolute/verification/verify.py","/absolute/python/bin/python3","uv.lock"],"environment":["PATH","VERIFY_MODE"],"nonce":"baseline-1"}'
```

This is an argument, not a standalone command. `version` must be `1`; `files` contains individual regular files, not directories or globs. Absolute paths identify external inputs; relative paths such as `uv.lock` resolve against the candidate verification workspace. Declare the external runner and relevant input/configuration files explicitly. Missing or unreadable inputs prevent accepting cached success. Keep the runner outside candidate write permissions, pin its interpreter and tools, and install dependencies using your locked process. A stable path or a lockfile alone does not prove that the installed environment is unchanged.

`environment` lists names, never `NAME=value` assignments. Identity evidence stores selected names and digests without their plaintext values; unset and empty values differ. The verifier uses a captured environment with `GIT_TERMINAL_PROMPT=0`. Digests are not password protection, and separately stored runner output can expose values it prints. Keep secrets out of argv, output and nonce labels.

A different `nonce` invalidates prior identity evidence. Use a non-secret label: configuration stores it as text, while identity evidence stores its digest. **Existing configuration is immutable through `Store.configure`, and there is no configuration-update CLI.** Choose the declaration and nonce when initializing new state. Repeating `init` cannot rotate an existing nonce; do not edit or delete canonical state to bypass this restriction. Existing state needs a separately supported configuration migration to change its declaration. An intentional content change to an already declared external input also invalidates its previous identity.

Cache reuse additionally requires the same HEAD, tree, command, timeout and a clean checkout. Legacy success without identity requires fresh verification; unknown identity versions are rejected. File content, resolved paths and metadata are observed before and after execution. This detects ordinary changes, including ordinary change-and-restore writes with changed metadata, but does not execute an immutable snapshot or defend against privileged metadata manipulation. Undeclared files, transitive dependencies, environment variables and remote services are not inferred. See the [self-hosting declaration and operating limits](examples/self-hosting/README.md#declare-verification-inputs-with-a-supporting-engine) for the external runner example.

## From the first TODO to a result

In your agent session:

```text
todo Add bounded retries for temporary network failures, with tests.
todo Show a useful error when a request cannot be retried.
trackpicks
```

The agent searches existing files for overlap and registers HTML plans. Review them in the dashboard. Pick tracks there and copy the command, or use the recommendations from `trackpicks`:

```sh
trackrun retry-backoff request-error-message
```

*These are example IDs; use the IDs returned by your own registration.* `todo` and `trackpicks` are agent skill requests; `trackrun` is also an installed terminal command.

| Step | What you can inspect |
|---|---|
| Register and review | The HTML plan, scope, evidence and acceptance conditions. [Example](examples/retry-backoff.html) |
| Select and execute | Selected IDs, separate worktrees, current workers and questions. |
| Verify and review | Verification output and independent review tied to the exact candidate. |
| Land and triage | With an authorized landing endpoint: integrated SHA, finding dispositions, issue closure and completion. |

The default endpoint is **`review`**, which preserves a reviewed candidate. To authorize automatic landing during initialization, add **`--endpoint land --allow-land`**. After landing, triage handles original-scope repairs, separate follow-ups and conditional watches. New follow-up TODOs await your selection.

## For agents

Read **[AGENT_INSTALL.md](AGENT_INSTALL.md)** and perform the requested installation and first-run scope. Reuse existing configuration and authorization. **Ask for the primary language if it was not specified; persist it with `init --language en|ko` and use it for new documents and reports.** Do not infer language solely from the English README.

Acceptance conditions should trace to the selected outcome or an existing invariant affected by the change. Keep optional features and unrelated defects separate, and honor recorded user tradeoffs during work and review. Discovering an improvement does not authorize adding it to the current track.

[Agent setup](AGENT_INSTALL.md) covers project discovery, authentication, language selection, skill installation, document review, execution and evidence-based handoff. [Skills](skills/) contain the task-specific instructions.

## How it fits together

```text
Your selection ── trackrun ── bounded, replaceable workers
                                ↕
                   Project files: goals, work, evidence
                                ↓
                  verify → review → authorized landing
                                ↓
                         triage → completion

Dashboard reads the same project state throughout.
```

One installed engine serves multiple projects. Each project has its own configuration, files, worktrees, installed skills and running dashboard/driver processes. Workers propose useful next work; the host validates ownership, evidence and external effects. There is no fixed global phase sequence or resident supervisor agent.

| Component | Support |
|---|---|
| Worker adapters | Claude CLI and Codex CLI; trusted command adapter for integrations/tests |
| Remote delivery | Git remote, optionally GitHub issues / PRs through `gh` |
| Project state | HTML / Markdown / JSON files; SQLite is only a rebuildable query cache |
| Language | English / Korean project preference and dashboard UI |
| Environment | Local macOS validation; Linux checks configured in CI. Windows is not supported by the current POSIX process/locking implementation. |

## Updates

```sh
todo-flow --version
todo-flow --state /absolute/project/todo compatibility --target /absolute/project/.agents/skills
```

For an installed uv tool, `todo-flow upgrade --wheel /absolute/new-release.whl --dry-run` plans an engine update; omit `--dry-run` to apply it while all drivers and dashboards are stopped. The updater checks known project formats, backs up the environment and restores it if installation or validation fails. Supply a trusted newer release wheel; automatic release discovery is not yet provided.

Then use `todo-flow --state STATE update-skills --target PATH --dry-run` for each project and apply without `--dry-run`. Local edits and language/state bindings are preserved; conflicting changes stop before any file is replaced. Engine and skill updates return separate rollback IDs.

[Update, rollback and recovery guide](UPDATES.md) includes interrupted-update recovery, legacy skill adoption, tested boundaries and the future release checklist.

## FAQ

**Does this replace my coding agent?** It coordinates selected work using your authenticated Claude or Codex CLI. You keep your model and project configuration.

**Where is the data? Is there a shared database?** Each project owns its `todo/` directory, or an explicit `--state` directory. Use `rg` to inspect it. No shared server or database service is required.

**What happens when a session or driver stops?** Restart `todo-flow --state STATE run` against the same files. The runtime reconciles claims and recorded effects. A stopped process is not reported as completion.

**Will it merge automatically?** The default is review-only. An initialized `land` endpoint with `allow_land` permits landing, followed by triage and completion checks. Existing branch protection still applies.

**How many tracks can I select?** Pass multiple IDs to `trackrun`. `--jobs` limits concurrent tasks for that driver (default: 2); it is not the number of selected tracks or a guarantee of a dedicated worker per track. There is no separate terminal-count admission limit, and historical terminal records do not block new workers.

**What does it cost?** TODO Flow is MIT licensed. Model usage and any external services follow your existing provider accounts and billing. Parallel work can increase model usage.

**Can I change language without changing the project?** Yes. The dashboard switch remembers a display preference for this project in this browser. It does not translate existing authored documents or change the agents' configured primary language.

**Do I need Jev?** No. The todo and watchlist skills recommend Jev for optional investigation/overlap/changed-source screening. They proceed without it; TODO Flow does not bundle or automatically install a Jev integration.

## Current scope

**New in 0.0.9:** Configure optional preflight and intermediate verification checks while preserving full verification and independent review at delivery boundaries. Bind supplied machine evidence to its condition, candidate and original artifact; inspect worker termination separately from proposal validity. Explicit request limits preserve attempt usage, and obsolete integration cleanup retains ownership and user-change protections.

**New in 0.0.8:** Activity groups current work by track, keeps long instructions in task details and preserves navigation across language changes. Identical pending obligations share one execution while retaining each parent request. Workers can propose bounded text replacements, and verification output is retained as separate log artifacts that support bounded reads.

**New in 0.0.6:** native workers reuse the existing Codex login without a version allowlist or credential-file restrictions. Terminal counts and historical launch records no longer block new workers, and deferred viewer cleanup preserves completed proposals. Release-only CI avoids repeating the full runtime suite.

**Added in 0.0.5:** declared verification-input identity, durable process cleanup and native Orca/Codex worker sessions. See [execution boundaries](OPERATIONS.md#review-landing-and-completion).

**New in 0.0.3:** integration repairs merge the current base into the candidate checkout, expose conflict evidence by path and require new verification and independent review before landing. Interrupted repairs and decision answers retain the recorded merge. See [repair behavior](OPERATIONS.md#review-landing-and-completion).

**New in 0.0.0.2:** workers read project files on demand, and `trackrun` prefers visible terminal logs through Orca, a configured terminal launcher or tmux. No available terminal means headless execution; `--launcher headless` explicitly selects it. The published `0.0.1` wheel still uses the earlier snapshot/headless implementation. See [worker execution](OPERATIONS.md#worker-context-and-terminal-launchers).

Completed tracks automatically clean disposable checkouts and unchanged worker terminals while retaining documents, logs, results and Git branches. Resources with user changes or unconfirmed ownership are kept with a reason. Use `--no-auto-cleanup` to retain resources for inspection; see [cleanup and retry](OPERATIONS.md#cleanup-migration-and-hooks).

Latest release: **0.0.9**. Small-project full cycles, recovery and two–three independent concurrent tracks have been exercised; large lists have separate synthetic UI coverage.

- One repository per project state. Forgejo, submodules and coordinated multi-repository landing are not implemented.
- Development workers explore the checkout with read-only tools and return JSON proposals. The runtime applies changes, verifies and publishes. Browser workflows are not implemented.
- Source contents and full evidence are not injected into the prompt; there is no aggregate 150,000-byte source limit on `main`. Provider context limits still apply to what a worker chooses to read. File deletion and binary edits are not supported.
- Workers have no default time limit. `init --worker-timeout SECONDS` opts into one; existing projects retain their configured limit (`worker_timeout: null` disables it). Cancellation and driver-loss cleanup remain active. Default driver task-assignment limit: 100; remaining requests survive for the next run.
- No shared slot budget across drivers, separate heavy-verification queue or validated distributed-filesystem operation.

## Documentation and contributing

[Updates and rollback](UPDATES.md) · [Operations and recovery](OPERATIONS.md) · [Demo and acceptance test](DEMO.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Agent repository rules](AGENTS.md) · [CI configuration](.github/workflows/ci.yml)

Report bugs or propose improvements through repository Issues; include a minimal public-safe reproduction. Contribution and local validation commands are in [CONTRIBUTING.md](CONTRIBUTING.md). Local design notes in `docs/` are ignored and are not required to use or build the project.

**[MIT License](LICENSE)** · Copyright © 2026 TODO Flow contributors.

Native Orca execution uses an owned managed checkout and a dedicated Codex App Server session. The visible Codex client attaches to the exact server/thread after turn acceptance; read-only proposals return through the server protocol. Explicit headless remains headless. Codex versions and credential storage do not select the execution route: the adapter reuses the existing Codex login and validates actual protocol responses. Existing Git checkouts and missing review provenance retain an explicitly reported compatibility route. CLI launch evidence and the dashboard show recorded workspace, session, turn and terminal associations. Synthetic protocol/process tests are included; external model acceptance testing is not claimed.
