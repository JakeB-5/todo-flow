# See TODO Flow in action

[English](DEMO.md) · [한국어](DEMO.ko.md) · [日本語](DEMO.ja.md) · [简体中文](DEMO.zh-CN.md)

[README](README.md) · [Agent installation](AGENT_INSTALL.md) · [Operations](OPERATIONS.md) · [Updates](UPDATES.md)

## This repository's own setup

The [self-hosting walkthrough](examples/self-hosting/README.md) records this repository's real September 26, 2026 setup: a separately installed `0.0.4` engine, Korean project language, Codex workers and a review endpoint. It includes the actual empty dashboard and a portable verification recipe. At that snapshot, no tracks had been registered or executed in this project. Future real task results belong alongside that setup record.

## Dashboard tour

![Dashboard tour](assets/demo/dashboard-tour.gif)

These are captures of the actual application using **synthetic, read-only data**, not evidence of model execution. The fixture contains 48 active and 2,500 completed tracks, including current work, decision waits and a conditional watch.

[English screenshot](assets/demo/dashboard-en.png) · [Korean screenshot](assets/demo/dashboard-ko.png) · [Activity](assets/demo/activity-en.png) · [Rich plan example](assets/demo/track-example.png)

From the source checkout, use a **new directory outside the repository**:

```sh
uv sync --frozen
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo --language en
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

Open `http://127.0.0.1:8766`:

1. Scroll the continuous active list, search and select two eligible tracks.
2. Copy the `trackrun` command; copying does not start work.
3. Open Activity to inspect owners, current tasks and decision waits.
4. Open a track to read its document, conditions and evidence.
5. Search the separate completed archive and load earlier records when needed.
6. Switch English / 한국어 / 日本語 / 简体中文. Selection and decision drafts are retained; authored content stays in its original language.

The fixture rejects dashboard mutation requests. Use a separately initialized project to execute real work.

To assemble the tour after capturing screens from a running browser:

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

The script combines supplied captures; it does not invent execution state.

## Run the full workflow

Use a disposable project with an initial commit, origin remote, authentication and working tests. Follow [setup](AGENT_INSTALL.md), selecting `en`, `ko`, `ja` or `zh-CN`. Use `--endpoint land --allow-land` if this exercise includes actual integration and triage; otherwise it stops at a reviewed candidate.

Give your agent two bounded requirements, for example:

```text
todo Add bounded retries for temporary network failures. Preserve permanent failure behavior and add tests.
todo Explain permanent request failures with useful next steps. Add message tests.
trackpicks
```

The [retry plan](examples/retry-backoff.html) and [error-message plan](examples/request-error-message.html) show reviewable HTML documents. Adapt their scope and evidence to the actual fixture before registering; they are examples, not completed work.

Review the generated documents, then request their actual IDs:

```sh
trackrun retry-backoff request-error-message
```

Observe the separate worktrees and tasks in the dashboard. Inspect real verification and independent review for each candidate. With landing authorized, check the integrated SHA, post-landing triage, issue closure and completion. If work waits on a decision, answer it and restart the driver when needed. New follow-up TODOs remain unselected.

## Inspect a previous public acceptance run

A disposable public test on September 24, 2026 produced:

| Requirement | Issue | Merged change |
|---|---|---|
| Text slugification | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| Sequence chunking | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| Numeric clamping | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

The first two tracks needed recovery after a triage duplicate-search defect was repaired. The fresh third track completed without intervention. These artifacts demonstrate the execution workflow at that time, not every later UI/localization change or large-project scale.

## Reproduce remote acceptance

The latest development run used path-based workers in visible Orca terminals on September 24, 2026:

| Requirement | Issue | Merged change |
|---|---|---|
| Collapse whitespace | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/1) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/4) |
| Preserve first distinct values | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/2) | [PR #5](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/5) |
| Division with explicit fallback | [Issue #3](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/3) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/6) |

Three implementation workers overlapped. Fourteen real Codex workers ran in Orca terminals, including repeated triage after base advancement. All three selected tracks completed; a new usage-document TODO and an existing license TODO remained unselected. The run had no decision waits or runtime errors, and the delivered fixture passed 19 tests. The fixture included a source file larger than 150 KB. These are bounded acceptance tasks, not a large-project benchmark or live Claude validation.

The completed run's 11 worktrees and 14 worker terminals were then removed, preserving the existing 466 evidence files and every local branch tip. A subsequent [cleanup lifecycle task (PR #8)](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/8) completed with real Codex workers: after landing and triage, its three worktrees and four worker terminals were automatically removed, leaving only the main checkout and retained evidence. [Issue #7](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/7) was closed by the workflow.

The following command **creates a public repository and real issues, PRs, model calls and merges**. Run it only when you intend that external experiment, using your own account and a new test directory:

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --launcher orca --register-orca --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

This variant also registers the disposable fixture in a running local Orca app and requires real terminals. Use `--launcher headless` and omit `--register-orca` for a headless run. The script registers reviewable HTML plans. The resulting report records actual tracks, remote artifacts, worker overlap, terminal receipts and input sizes. Preserve initial failure, recovery and fresh-run outcomes separately. See [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance) for the experiment boundary.
