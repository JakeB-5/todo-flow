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
| `src/todo_flow/web/` | Dashboard HTML, CSS, JS and English/Korean/Japanese/Simplified Chinese messages |
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

English is the source language of public documentation, templates and shared skill instructions. The supported locales are English (`en`), Korean (`ko`), Japanese (`ja`) and Simplified Chinese (`zh-CN`). Keep the same locale set across project setup, dashboard messages and the five core guides.

| Surface | Required coverage |
|---|---|
| Dashboard fixed text, accessible names, dates and numbers | en, ko, ja, zh-CN; English fallback for unsupported locales or messages |
| Project default and requested language of new worker output | en, ko, ja, zh-CN |
| README, AGENT_INSTALL, OPERATIONS, UPDATES, DEMO | English `NAME.md` and `NAME.ko.md`, `NAME.ja.md`, `NAME.zh-CN.md` |
| CONTRIBUTING, shared skills and templates | English maintenance instructions; full translation is not required |
| Existing authored tracks/records, historical CHANGELOG and LICENSE | No automatic or retrospective translation |

Project setup stores `language: en|ko|ja|zh-CN`. Agent-assisted setup asks when the user has not chosen; unattended CLI setup defaults to English. Skills use the project language for reports and newly authored documents. Machine keys, IDs, code conventions and quoted source remain stable. A requested output language is not proof of a model's translation quality.

Dashboard messages use English source text through `tr()` and locale dictionaries in `src/todo_flow/web/i18n.js`. Static labels use `data-i18n` and accessible attributes use `data-i18n-aria-label`, `data-i18n-title` or `data-i18n-placeholder`. Use named interpolation for complete messages, retaining every placeholder in each locale. Never translate stored user content by matching its text. Verify that language switching preserves selections, drafts and navigation and that browser preferences stay project-specific without changing the project language.

### Translation updates

Each translated core guide records its English source and the SHA-256 of that source's exact bytes:

```html
<!-- translation-source: NAME.md; source-sha256: LOWERCASE_SHA256; status: translated -->
```

`translated` records the translator's claim of coverage at that hash, not an independent certification. A missing marker or changed source hash needs review. The checker reports stale translations; it never rewrites hashes or translates content.

1. Edit the English source first. Compare its changes with all three translations, including navigation-only changes. Preserve the installation requirements, authorization boundaries, compatibility constraints, recovery, rollback and cleanup instructions.
2. Translate the changed meaning and check the full affected section. Keep functional commands, flags, paths, protocol keys, skill names and placeholders intact. Shell comments and explanatory prose may be translated. Do not shorten an operational restriction into a general summary.
3. Keep four sibling-language links in every guide and links to all other core guides in the same language. Preserve referenced section anchors; explicit `<a id="english-section-id"></a>` anchors allow translated headings to retain stable links. Check relative file and fragment links, including links outside the core set.
4. Only after checking the translation, calculate the English file's raw-byte hash with `python -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("NAME.md").read_bytes()).hexdigest())'` and update the marker in each reviewed translation. Do not refresh a hash just to silence a stale report. Keep pending translations visibly unresolved until updated.
5. Run the document checker and localization tests below. Have an independent reviewer compare meaning with the English source, especially commands, permissions and recovery instructions. Structural checks cannot establish fluency, completeness of meaning or translation quality.
6. Keep all 20 guide files in the source distribution. After building, inspect the actual archive using `--sdist`; a correct source tree alone does not prove a correct release artifact.

Use this glossary for prose; retain the English token when referring to a literal CLI command, protocol field, status or skill name.

| English concept | Korean | Japanese | Simplified Chinese |
|---|---|---|---|
| track | 트랙 | トラック | 轨道 |
| worker | 워커 | ワーカー | 工作器 |
| dashboard | 대시보드 | ダッシュボード | 仪表盘 |
| verification | 검증 | 検証 | 验证 |
| review | 리뷰 | レビュー | 审查 |
| landing | 합입 | 取り込み | 合入 |
| cleanup | 정리 | クリーンアップ | 清理 |
| recovery | 복구 | 復旧 | 恢复 |
| rollback | 롤백 | ロールバック | 回滚 |
| source of truth | 정본 | 正本 | 权威来源 |

Jev guidance in todo/watchlist is optional. Do not turn a recommendation into an installation dependency or mandatory review gate.

## Checks

Runtime tests must isolate registration and maintenance state as well as project files. Call `runtime_home.isolate_runtime_home(self)` from `tests/runtime_home.py` at the start of a unittest fixture's `setUp`, before calling the CLI, engine, dashboard or updater. It replaces an inherited `TODO_FLOW_HOME` with a fresh temporary home, passes it to child processes through the environment, and registers cleanup that restores the caller's value (or absence), including when `setUp` fails. Register temporary project directories and child shutdown with `addCleanup` too, so children stop before the home is restored or removed. Fixtures used through direct `setUp`/`tearDown` calls must call `doCleanups` from `tearDown`; direct callers must also use `doCleanups` if setup fails.

Keep the explicit temporary homes in update and skill coexistence tests. Nested fixtures must finish in reverse order. Set the home before starting threads or children and join/stop them before cleanup; all concurrent work inside one fixture shares that home. Run independent fixtures concurrently in separate processes, since `os.environ` is process-wide. Child calls that supply `env` must copy the fixture environment before adding overrides. Do not rely on teardown to remove registrations from a user's home: a killed test must leave any residual state only in its temporary directories. The standard discovery command and individual test modules use the same fixtures; no shell-level home override is required.

```sh
uv run python scripts/check_translations.py
uv run python -m unittest discover -s tests -p test_document_translations.py -v
uv run python -m unittest discover -s tests -v
uv run python -m unittest discover -s tests -p test_language.py -v
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
node --check src/todo_flow/web/app.js
node --test tests/dashboard_i18n.test.cjs
uv build
uv run python scripts/check_translations.py --sdist dist/todo_flow-0.1.1.tar.gz
```

Test observable behavior and relevant recovery boundaries. Do not add tests that merely repeat documentation wording. For UI changes, inspect the real browser in all four languages and at narrow widths. Synthetic list rendering, worker concurrency and remote PR merging are different checks; do not substitute one for another.

The document checker detects missing translations, stale or missing source markers, missing language or same-language guide links, broken relative files/fragments and omitted source-distribution entries. Its fixtures exercise those failures. The dashboard tests separately check missing message keys, interpolation and language-switch behavior. Neither check replaces semantic translation review.

Packaging changes should work without ignored local files. Verify the source distribution includes shared guides, license and examples, and that wheels contain the dashboard, translations, skills and template assets. Installed skills must work outside this source checkout.

Normal code changes run one Linux/Python 3.11 job, using uv 0.10.11 and explicitly installed ripgrep. **Actions → Checks → Run workflow → full** runs the Linux/macOS × Python 3.11/3.13 matrix when cross-platform validation is needed. Report local and hosted validation separately.

Automatic checks run on pushes to `main` and pull requests, except root-level Markdown guides, `assets/` presentation files and local `docs/` changes alone. `scripts/ci_changes.py` compares metadata contents: changing only the project version in `pyproject.toml`, the editable package version in `uv.lock`, and the fallback version in `release.py` runs lint, build and clean wheel installation checks without the runtime suite. Dependency, build configuration and real source changes still run tests. Bundled skill and updater changes also run the isolated update smoke, once on Python 3.11 per selected OS. Run the focused document checks explicitly for guide-only changes skipped by CI.

Tag pushes do not repeat release-commit CI. Newer runs cancel older runs for the same event and branch or pull request. Before making these checks required in branch protection, add an always-reported gate: workflow-level path skips leave required checks pending.

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

For `0.1.1`, keep `pyproject.toml`, `uv.lock` and all four README versions aligned. Run the checks above, then build into a version-specific directory so previous development artifacts are not accidentally published:

```sh
uv lock
uv build --out-dir dist/0.1.1
uv run python scripts/check_translations.py --sdist dist/0.1.1/todo_flow-0.1.1.tar.gz
```

Verify the wheel installs in a clean environment and includes skill templates, dashboard translations and the MIT license. Confirm the source distribution includes the public guides and excludes local `docs/`. Keep the changelog under `Unreleased` until publication. Tagging, GitHub Releases and package-index uploads are separate release actions.


## Update compatibility checks

Maintain `src/todo_flow/release.json` independently from the package version. Bump a state/config/protocol contract only with a documented compatibility or migration path. Never label a format compatible just to make an update pass.

Run `uv run python -m unittest discover -s tests -p test_updates.py -v` for update boundaries. After `uv build --out-dir dist/update-check`, run `uv run python scripts/update_smoke.py --artifacts dist/update-check --root /absolute/new-directory` for isolated real uv tool replacement, rollback and recovery. This needs no model credentials or GitHub mutations. The script derives two later test versions from the current package version. Those wheels are local fixtures and must never be published.

Add tests for changed dependencies, mixed versions and interrupted migrations when those behaviors are introduced. [UPDATES.md](UPDATES.md) separates implemented update support from the remaining release checklist.
