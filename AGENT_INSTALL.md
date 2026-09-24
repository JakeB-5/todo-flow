# TODO Flow — agent installation and first run

Follow the user's requested scope. Reuse existing installation, configuration and authorization. This document is not independent permission to execute work or land changes. Complete authorized reversible preparation; ask only for missing decisions or authentication the user must perform.

## 1. Confirm the target and language

Distinguish the TODO Flow checkout from the user's target project. Read the target's `AGENTS.md`, development instructions, verification commands, Git remote, base branch, existing skills and state configuration.

**Primary language is a user choice: English (`en`) or Korean (`ko`).** Reuse an existing project language or an explicit preference in this request. Otherwise ask the user which language to use. Continue independent environment inspection while waiting, but do not silently decide from the language of this README. Skill instructions remain English; reports, questions and newly written documents use the selected language unless explicitly overridden.

```sh
FLOW_SOURCE='/absolute/todo-flow'
FLOW_PROJECT='/absolute/my-project'
FLOW_STATE="$FLOW_PROJECT/todo"
FLOW_SKILLS="$FLOW_PROJECT/.agents/skills"

git -C "$FLOW_PROJECT" rev-parse --show-toplevel
git -C "$FLOW_PROJECT" status --short
git -C "$FLOW_PROJECT" rev-parse --verify HEAD
```

For Claude sessions, use `.claude/skills`. Variables may not survive separate shell calls: redeclare them or use absolute paths. If `todo/` belongs to a different tool, select a separate state directory rather than overwriting it. Preserve existing changes, Git history and skill installations.

## 2. Prepare tools and authentication

Check Python 3.11+, uv, Git, and the chosen authenticated Claude/Codex CLI. GitHub integration also needs authenticated `gh` and access to the actual target remote. Do not substitute a model without a reason in the user's request. Keep credentials out of documents, configuration and output.

For a fresh release installation, no checkout is required:

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.1/todo_flow-0.0.1-py3-none-any.whl
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --version
trackrun --version
```

The official repository is [https://github.com/JakeB-5/todo-flow](https://github.com/JakeB-5/todo-flow); release artifacts and checksums are on [GitHub Releases](https://github.com/JakeB-5/todo-flow/releases). If source development was requested, install from the TODO Flow checkout instead:

```sh
cd "$FLOW_SOURCE"
uv sync --frozen
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --help
trackrun --help
```

Reuse a compatible installation. Diagnose PATH before reinstalling. Before replacing a shared engine, consider other running projects. Use the documented GitHub release wheel; do not assume a same-named package on another index is this project.

Unreleased `main` adds on-demand file reads and terminal-first workers; these are not in the `0.0.1` release wheel. When those capabilities are requested, use the source-development path and state the installed version/source accurately. The new launcher defaults to `auto` (Orca, configured terminal or existing tmux, then headless); do not force headless merely because the worker is automatic. See [worker execution](OPERATIONS.md#worker-context-and-terminal-launchers-unreleased).

## 3. Configure the project

If initialized, read the existing state configuration and reuse its worker, language, verification, scope and endpoint. Do not rerun `init` or edit execution configuration during a run.

For a new project, obtain values from the actual project:

| Setting | Source |
|---|---|
| `--repo`, `--state` | Target Git root and separate canonical project state |
| `--language en` / `--language ko` | User's selected primary language |
| `--base`, optional `--github` | Actual remote/base and GitHub owner/repository |
| `--worker` | User's selected or available authenticated Claude/Codex CLI |
| `--verify` | An existing, working verification command expressed as JSON argv |
| `--context`, `--write` | Exploration hints on development `main` (snapshot selection in `0.0.1`) and authorized write patterns |
| Endpoint | Default `review`; use `--endpoint land --allow-land` when landing is already authorized |

Run the verification command before configuring it. Report pre-existing failures rather than hiding them. Exclude secret files. An initial commit, Git author identity and `origin` are required for execution. Do not bootstrap a missing remote, commit unrelated work or reset history without a request that covers it.

Example for a Python project; replace the values and language:

```sh
todo-flow --state "$FLOW_STATE" init \
  --repo "$FLOW_PROJECT" --base main --worker codex --language en \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md
```

Add `--github OWNER/REPOSITORY` only for the supported GitHub adapter. Forgejo and combined multi-repository delivery are not implemented. Protect runtime records from accidental commits using the project's existing policy or local Git excludes. Do not untrack an existing versioned track ledger.

## 4. Install skills and open the dashboard

```sh
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS"
todo-flow --state "$FLOW_STATE" serve --port 8765
```

The installer preserves existing directories and records language/state in each installed skill's `project.json`. It inherits the initialized project language. Standalone installation supports `--language en|ko`; an explicit value conflicting with an initialized project is rejected. Do not replace an entire skills directory to resolve one conflict. For an existing installation, use `update-skills --target PATH --dry-run`, review conflicts, then apply only within the requested update scope. See [UPDATES.md](UPDATES.md) for manifest adoption and rollback.

Run the dashboard in a persistent terminal/process. If a port is occupied, use a free one without killing an unrelated server. Verify the real URL, project and default language. The display switch is browser-local and project-specific; it does not change worker language or translate historical documents.

If the current agent session does not discover newly installed skills, read the installed `SKILL.md` directly and explain whether a new session is needed for discovery.

## 5. Register a first real requirement

For setup-only requests, provide the dashboard URL and an example `todo [requirement]` request. Do not create arbitrary sample tracks in a working project. If first-run execution was requested but no requirement was supplied, ask for the desired change.

Read the installed todo skill. Investigate the actual requirement, search existing tracks and watches, and write a reviewable HTML document from the installed template outside canonical state. Use the selected language in the visible document and structured prose; set `language` and HTML `lang` to `en` or `ko`. Keep IDs and schema keys stable.

```sh
todo-flow --state "$FLOW_STATE" register /absolute/scratch/first-track.html
```

Include `--assets` when needed. Use the actual returned ID and revision for `http://127.0.0.1:PORT/documents/ID/REVISION/index.html`. Inspect the registered document's browser rendering and representative interactions. Provide its link. Do not claim visual verification without performing it.

The todo and watchlist skills recommend optional Jev assistance. Start and finish the requested work with available tools; missing Jev is not a setup blocker. Do not install it or request credentials as an unrequested prerequisite.

## 6. Select, run and hand over

Use track-picks to examine current state, dependencies and available scope. If the user already asked for selection and execution of this first requirement, proceed within that scope. Registration or recommendation alone does not authorize execution.

Read trackrun and execute the actual registered ID:

```sh
trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID
```

`--jobs` is optional. If using `--request-only`, verify a separate driver is running; request persistence alone is not a completed first run.

Inspect `state.json`, `tasks`, `attempt-records`, `results`, `decisions` and `effects`. For GitHub, compare actual issue/PR state with receipts. A `review` endpoint produces a reviewed candidate. A `land` endpoint requires the landed SHA, current cleared triage, completed track and issue closure when an issue exists.

For interruptions, inspect evidence and resume against the same state; do not reinitialize. Record genuine user answers with `answer`, then run the driver if needed. Technical errors are not invented user decisions. Preserve failures and distinguish recovery with intervention from a clean run. New follow-up TODOs await selection.

Hand over in the selected language: **installation and state paths, language, dashboard/document links, actual outcome and issue/PR links, remaining decisions and next command**. Identify any terminal/process left running.


## Updating an existing installation

Read [UPDATES.md](UPDATES.md). Inspect installed versions and project compatibility first. Preserve the current primary language, state binding, user edits and previous execution authority. Updating alone does not authorize new tracks or landing.

For uv tool installs, use the guarded `upgrade --wheel` path with an explicitly selected trusted release. Let the user/driver finish or pause active work and stop dashboards before replacement. Do not bypass a maintenance conflict with direct package-manager replacement. Keep the printed recovery command and returned backup IDs. Update installed project skills with a dry run first; conflicts require reconciliation, not directory deletion or forced replacement. Validate the new engine, skills and existing documents, then restart only the processes covered by the user's request.

Automatic network version discovery is not currently provided. A source checkout should use its existing update/install workflow while idle, then the same compatibility and skill checks. If the CLI disappears after an interrupted update, use the saved base-Python recovery runner outside the replaced environment.
