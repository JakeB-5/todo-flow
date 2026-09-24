# See TODO Flow in action

[README](README.md) · [한국어](README.ko.md) · [Operations](OPERATIONS.md)

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
6. Switch English / 한국어. Selection and decision drafts are retained; authored content stays in its original language.

The fixture refuses mutating dashboard requests. Run real work in a separate initialized project.

To assemble a tour after capturing screenshots from the running browser:

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

The script only combines supplied captures; it does not manufacture execution states.

## Run the full workflow

Use a disposable project with an initial commit, an origin remote, authentication and working tests. Follow [setup](AGENT_INSTALL.md), choose `en` or `ko`, and use `--endpoint land --allow-land` if this exercise should include real landing and triage. Without those flags, it ends at a reviewed candidate.

Give the agent two bounded requirements, for example:

```text
todo Add bounded retries for temporary network failures. Preserve permanent failures and add tests.
todo Explain permanent request failures with a useful next action. Add message tests.
trackpicks
```

[Retry plan example](examples/retry-backoff.html) and [error-message plan example](examples/request-error-message.html) illustrate reviewable HTML documents. Adapt scope and evidence to the real fixture before registration; these files are illustrative requirements, not completed work.

Review the generated documents, then request the actual returned IDs:

```sh
trackrun retry-backoff request-error-message
```

Observe separate worktrees and tasks in the dashboard. Inspect the actual verification and independent review for each candidate. With authorized landing, compare the integrated SHA, post-landing triage, issue closure and completed state. If work waits for a decision, answer it and restart a driver if needed. Follow-up TODOs remain unselected.

## Inspect a previous public acceptance run

A disposable public test on September 24, 2026 produced these artifacts:

| Requirement | Issue | Merged change |
|---|---|---|
| Text slugification | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| Sequence chunking | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| Numeric clamping | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

The first two tracks needed recovery after a triage duplicate-search bug was corrected. A new third track completed without additional intervention. These artifacts demonstrate the earlier execution workflow; they do not validate every later UI/localization change or large-project scale.

## Reproduce remote acceptance

This command **creates a public repository and real issues, PRs, model calls and merges**. Use your account and a new test directory only when that external experiment is intended:

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

The resulting report records actual tracks, remote artifacts and worker overlap. Preserve initial failure, recovery and fresh-run outcomes separately. See [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance) for the experiment boundary.
