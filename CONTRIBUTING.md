# Contributing to TODO Flow

Keep the runtime, skills, templates and public documentation consistent. See [README](README.md) for supported behavior and [CHANGELOG](CHANGELOG.md) for user-visible changes. Contributions are distributed under the [MIT License](LICENSE).

## Development setup

Python 3.11+, uv, Git and ripgrep (`rg`) are required. Node.js runs dashboard syntax and localization checks. Local automated tests use temporary Git repositories and do not require model or GitHub credentials.

```sh
uv sync --frozen
uv run todo-flow --help
```

Keep dependency changes in `pyproject.toml` and `uv.lock` together.

| Path | Responsibility |
|---|---|
| `src/todo_flow/` | CLI, file authority, execution, recovery and adapters |
| `src/todo_flow/web/` | Dashboard HTML, CSS, JS and English/Korean messages |
| `skills/` | Agent registration, selection, execution, review, landing, triage and Watch instructions |
| `templates/`, `examples/` | Starter documents and public-safe review examples |
| `tests/` | Unit, local Git integration, HTTP and dashboard localization tests |
| `scripts/` | Reproducible acceptance tests and labeled dashboard fixtures |
| `assets/` | Public demo captures and anonymous aggregate charts |
| `docs/` | Local-only design and experiment records; ignored and excluded from distributions |

Edit canonical skills under `skills/`, not installed copies. `todo-flow install-skills --target PATH` installs them per project without overwriting existing directories.

## Preserve the workflow

- Registration belongs to todo, selection to the dashboard/track-picks, and execution to explicit trackrun requests. Do not add dashboard authoring or execution requests.
- Documents and runtime records are files. SQLite is a rebuildable cache; agents do not need SQL tools.
- Keep host ownership checks, generation fencing, exact-head verification, independent review and external-effect receipts intact.
- Preserve interruption, failure and recovery evidence. Reconcile remote effects before retrying after a lost response.
- Confirmed landing needs current cleared triage before completion. Follow-up TODO registration does not authorize execution.
- Preserve HTML/Markdown source and revisioned assets. Do not flatten interactive plans into text-only summaries.
- Keep public examples and screenshots independent of private project identities or content.

## Language and skills

English is the primary language of public documentation, templates and skill instructions. Keep `README.ko.md` aligned with the main README.

Project setup stores `language: en|ko`. Agent-assisted setup asks when the user has not chosen; unattended CLI setup defaults to English. Skills use the project language for reports and newly authored documents. Machine keys, IDs, code conventions and quoted source remain stable.

Dashboard messages use English source text through `tr()` and Korean translations in `web/i18n.js`. Static labels use `data-i18n` and accessible attributes use `data-i18n-aria-label`, `data-i18n-title` or `data-i18n-placeholder`. Use named interpolation for complete messages. Never translate stored user content by matching its text. Verify that language switching preserves selections, drafts and navigation and that browser preferences stay project-specific.

Jev guidance in todo/watchlist is optional. Do not turn a recommendation into an installation dependency or mandatory review gate.

## Checks

```sh
uv run python -m unittest discover -s tests -v
uv run python -m unittest discover -s tests -p test_language.py -v
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
node --check src/todo_flow/web/app.js
node --test tests/dashboard_i18n.test.cjs
uv build
```

Test observable behavior and relevant recovery boundaries. Do not add tests that merely repeat documentation wording. For UI changes, inspect the real browser in both languages and at narrow widths. Synthetic list rendering, worker concurrency and remote PR merging are different checks; do not substitute one for another.

Packaging changes should work without ignored local files. Verify the source distribution includes shared guides, license and examples, and that wheels contain the dashboard, translations, skills and template assets. Installed skills must work outside this source checkout.

GitHub Actions is configured for macOS and Linux with Python 3.11 and 3.13, using uv 0.10.11 and explicitly installed ripgrep. Configuration alone does not establish a successful hosted run; report local and hosted validation separately.

Automatic checks run on pushes to `main` and on pull requests, except when all changed files are root-level Markdown guides, `assets/` presentation files or local `docs/`. Mixed documentation/code changes still run the full matrix. Skill Markdown under `skills/`, runtime/dashboard code, templates, tests, scripts, dependencies and workflow configuration remain covered. Feature-branch pushes are checked through their pull request; tag pushes do not repeat the matrix already run for the release commit.

Use **Actions → Checks → Run workflow** when a full matrix is needed regardless of changed paths, including release preparation after documentation-only changes. A newer run cancels an older run for the same event and branch or pull request. Before making these checks required in branch protection, add an always-reported gate: GitHub's workflow-level path skips leave required checks pending.

## Demos and external acceptance

[DEMO.md](DEMO.md) documents the local UI fixture and full workflow exercise. The fixture is synthetic and read-only, with 48 active and 2,500 completed tracks. It makes no model calls or remote changes.

Live acceptance commands create real model usage, GitHub issues, PRs and landing effects. Run them only within explicitly requested or previously authorized disposable test scope:

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

Use newly created public-safe fixtures. Never copy private code, plans, assets or raw logs into a public test. Compare remote results with file records, preserving initial failures and intervened recovery in the report.

## Documentation, commits and PRs

Put shared instructions in tracked root guides or relevant code/skill files. Preserve ignored `docs/` files and never force-add them. Keep credentials, runtime state, transcripts, virtual environments and build outputs out of Git.

Metric assets contain anonymous aggregates and formulas only. Keep raw Git history, source identities, paths, authors, commit messages and code outside this repository. Distinguish predecessor observations from current package benchmarks.

Use focused imperative commit subjects, such as `Preserve selection when switching dashboard language`. PRs should explain the problem, resulting behavior, validation and remaining limitations. Link related issues and include public-safe screenshots for visible changes. State which checks could not run.

Record behavior, compatibility and important fixes under `Unreleased`. Prepare the package version before release; assign a changelog release date and publish tags only when releasing. A local package version is not evidence of a remote release.


## Release preparation

The initial distribution is a Python package containing the `todo-flow` and `trackrun` CLIs, dashboard assets and installable project skills. It does not include a native agent-plugin manifest or marketplace package.

For `0.0.3`, keep `pyproject.toml`, `uv.lock` and both README versions aligned. Run the checks above, then build into a version-specific directory so previous development artifacts are not accidentally published:

```sh
uv lock
uv build --out-dir dist/0.0.3
```

Verify the wheel installs in a clean environment and includes skill templates, dashboard translations and the MIT license. Confirm the source distribution includes the public guides and excludes local `docs/`. Keep the changelog under `Unreleased` until publication. Tagging, GitHub Releases and package-index uploads are separate release actions.


## Update compatibility checks

Maintain `src/todo_flow/release.json` independently from the package version. Bump a state/config/protocol contract only with a documented compatibility or migration path. Never label a format compatible just to make an update pass.

Run `uv run python -m unittest discover -s tests -p test_updates.py -v` for update boundaries. After `uv build --out-dir dist/update-check`, run `uv run python scripts/update_smoke.py --artifacts dist/update-check --root /absolute/new-directory` for isolated real uv tool replacement, rollback and recovery. This needs no model credentials or GitHub mutations. The script derives two later test versions from the current package version. Those wheels are local fixtures and must never be published.

Add tests for changed dependencies, mixed versions and interrupted migrations when those behaviors are introduced. [UPDATES.md](UPDATES.md) separates implemented update support from the remaining release checklist.
