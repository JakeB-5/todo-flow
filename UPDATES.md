# Updating TODO Flow

[README](README.md) · [Agent installation](AGENT_INSTALL.md) · [Operations](OPERATIONS.md)

Update the shared engine once, then update the installed skills of each project. Project documents and execution records remain in their existing state directory. Updating does not reinitialize a project or start pending work.

## Update capabilities

| Capability | Behavior |
|---|---|
| Version reporting | `todo-flow --version` and `trackrun --version` report the installed package version. |
| Compatibility inspection | `compatibility` reports engine, state/config formats, worker protocol and optional installed skill differences. |
| Engine update | `upgrade --wheel` replaces a uv tool installation with an explicitly supplied newer local release wheel. |
| Runtime exclusion | Cooperative CLI operations, drivers and dashboards hold process locks. An engine update requires all of them to stop; a skill update requires its project to be idle. |
| Skill update | Installation manifests record version, protocol and SHA-256 file baselines. Updates compare the baseline, current files and the new bundle. |
| Local edits | Keep custom files, project language/state bindings and edits to files unchanged by the new bundle. Conflicting changes stop the entire update before modification. |
| Backup and rollback | Engine environments/entrypoints and affected skill directories are backed up before replacement. Rollback is explicit; a failed update restores its before-image. |
| Interrupted update | Durable pending markers block normal use of an incomplete engine update. A recovery runner remains outside the replaced environment. Skill updates have their own recovery marker. |
| Forward compatibility | Unknown state/config formats and worker/skill protocols are rejected. A future-format pending state journal is never applied by an older reader. |

The `0.0.1` release contracts are **state format 1, configuration format 1, worker protocol 1 and skill protocol 1**. Package versions and data formats are independent. Older file-backed projects without the new optional configuration metadata use format/protocol 1. No data migration is necessary for this release. The existing explicit SQL-to-files migration remains a separate command.

Version `0.0.2` initializes **worker protocol 2** for path-based inputs. Newly initialized project configurations require engine `0.0.2` or later. State/configuration/skill formats remain 1. The built-in Claude/Codex adapters accept existing project configurations and produce the new path-based input without rewriting state. Custom command adapters must be updated to read `workspace` and `paths`, and explicitly opt into protocol 2 while stopped; protocol-1 custom workers fail before spawning. Older release engines cannot run newly initialized protocol-2 projects. Terminal selection is a separate optional setting (`worker_launcher`, default `auto`); `--launcher` changes only the current driver. See [worker execution](OPERATIONS.md#worker-context-and-terminal-launchers).

Version `0.0.3` fixes integration repair handoffs and recovery without changing these formats or protocols. No state migration is required. The repair instructions are bundled with the engine; check each project’s installed skills using the normal update procedure.

## 1. Inspect and stop relevant processes

```sh
todo-flow --version
trackrun --version
todo-flow --state /absolute/project/todo compatibility \
  --target /absolute/project/.agents/skills
```

Let work finish or pause it, then stop the driver and dashboard normally. The updater does not kill workers for you. Unresolved running tasks and recorded live worker PIDs also block updates; inspect them and use `reconcile` after a stopped driver's claims have expired. A queued request is preserved, not executed by an update.

Normal use registers project state paths under `TODO_FLOW_HOME` (default `$XDG_STATE_HOME/todo-flow`, or `~/.local/state/todo-flow`). The engine updater checks these known projects against the candidate's compatibility manifest. Projects never used with this runtime are not discoverable automatically: inspect them explicitly before switching versions.

All cooperating processes must use the same `TODO_FLOW_HOME`. Process locks are a coordination boundary, not a sandbox. Older processes without the guards, direct package-manager commands, source edits and custom integrations can bypass them; stop those processes before updating. Windows and distributed filesystems are outside the current support scope.

## 2. Update the shared engine

This path requires an existing **`uv tool install` installation** and `uv` on PATH. Source checkouts, editable environments, ordinary virtualenv installations and uv tool installs with custom extra requirements/options or entrypoints are diagnosed rather than overwritten. Update those environments using their original workflow while idle, then run project compatibility and skill checks.

Download the wheel and `SHA256SUMS` from the [v0.0.3 release](https://github.com/JakeB-5/todo-flow/releases/tag/v0.0.3), verify its checksum, then pass the local wheel path:

```sh
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.0.3-py3-none-any.whl --dry-run
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.0.3-py3-none-any.whl
```

The plan shows versions, artifact digest, compatibility contracts and known projects. Execution rechecks those facts under an exclusive runtime lock, snapshots the artifact, backs up the installed environment and two entrypoints, invokes uv, and checks the installed version, entrypoints and bundled skills. An ordinary install/validation failure restores the previous environment. Upgrades do not change project configuration, documents, claims or remote state.

The returned `backup` identifies the engine receipt. To restore it later:

```sh
todo-flow upgrade --rollback ENGINE_BACKUP_ID
```

Rollback requires the expected current release and checks that the old engine can still read known project data. It never reverses code landing or remote effects, and it does not roll back project state. Engine and project skill rollbacks are separate operations.

## 3. Update each project's installed skills

Use `.claude/skills` for Claude or `.agents/skills` for the corresponding project installation:

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills
```

The installed `project.json` can supply the state path when `--state` is omitted. Existing bindings and their exact contents are preserved. Additional project skills outside the managed bundle are untouched. Removed bundled resources are deleted only when they still match the original baseline; custom files in a retired skill directory remain.

If both you and the release changed a managed file, the command lists the conflict and applies nothing. Reconcile your edit with the new bundle before retrying. There is no blanket force-overwrite option. Start a new agent session when needed so it discovers the updated skill instructions.

To restore a completed update:

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --rollback SKILL_BACKUP_ID
```

Rollback refuses if files changed after the update, so it cannot silently erase later user edits. Preserve/reconcile those changes first.

### Installations created before manifests

Existing untracked skill directories are not guessed to be pristine. With the **matching original bundle**, run:

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt
```

Adoption requires the old managed files to exactly match that bundle. If they differ, establish the original version and reconcile the files rather than deleting the installation. Once adopted, later updates have a reliable baseline.

## 4. Recover an interrupted update

```sh
# When the installed CLI still starts:
todo-flow upgrade --recover

# For a project's skills:
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --recover
```

Before engine replacement, the command prints an absolute **base-Python + recovery-runner command**. Keep that line. The runner lives under `TODO_FLOW_HOME/engine-updates/ID/engine_updates.py`, outside the environment being replaced. Run the printed command with `--recover` if the CLI entrypoint disappeared mid-update. It uses the durable receipt to restore the previous installation; it needs no running dashboard or model.

Receipts, immutable before-images and displaced directories are retained for inspection. They may contain local paths and customized skill content; keep them out of Git and public reports. Automatic retention cleanup is not yet implemented. Do not remove a pending marker manually to bypass recovery.

## Verification

Unit tests cover conflicts, user-edit preservation, retired resources, exact rollback, partial failure, recovery, symlink refusal, runtime exclusion and unsupported formats. The isolated acceptance script builds synthetic future releases and exercises real uv tool replacement without touching a normal installation:

```sh
uv build --out-dir dist/update-check
uv run python scripts/update_smoke.py --artifacts dist/update-check --root /absolute/new-update-test-directory
```

It checks an active dashboard blocking upgrade, a successful engine update, skill update/rollback, engine rollback, automatic recovery from a deliberately broken release, and recovery with a missing CLI. It also compares every canonical state file before/after and confirms queued work was preserved without execution. No model calls, issues, PRs or remote mutations are needed. Synthetic future wheels are test artifacts, not releases to publish.

## Further preparation for future releases

These are follow-up items, not capabilities already provided by the current implementation.

| Priority | Preparation | Reason / completion criterion |
|---|---|---|
| Before public release | Select the authoritative distribution channel and package/repository names | Publish one canonical installation URL and one upgrade source; verify name ownership before advertising package-index installation. |
| Before public release | Immutable release versions, checksums and a reproducible tag/build relationship | Let users verify the downloaded wheel. The updater's digest detects artifact changes, but is not a publisher signature. |
| Before public release | Exercise the update acceptance job on supported hosted runners | Local success does not substitute for the configured Linux/macOS matrix. |
| Before any data-format change | An explicit migration registry with preflight, state backup, resumable checkpoints and downgrade rules | Define a real old→new transformation and test interruption before shipping it. Never infer migrations from package version alone. |
| Next | Version discovery and stable/preview channels | Show available releases and changes; resolve an exact artifact before acquiring update locks. |
| Next | Signed release provenance and trusted publishing | Establish who produced an artifact, beyond matching a supplied file's hash. |
| Next | Dependency/Python compatibility and rollback coverage | Test real dependency changes and interpreter transitions, not only same-contract package versions. |
| Next | Project inventory management and batch updates | List, forget or relocate known projects; provide per-project plans and receipts instead of guessing from folders. |
| Next | Project/skill protocol migration and mixed-version support policy | Define how long older installed skills and workers remain compatible with each engine. |
| Next | Graceful draining and stronger worker-process identity | Improve long-running update scheduling and distinguish a recorded PID from PID reuse using process start identity. |
| Next | Backup retention, disk-space checks and interrupted-backup cleanup | Keep recovery available without unbounded local storage growth. |
| Later | Offline release bundles, proxy/index configuration and organization rollout controls | Make installs reproducible where dependencies cannot be fetched freely. |
| Later | Additional operating-system support | Replace POSIX-specific locking/process assumptions before claiming Windows support. |

Automatic latest-version downloads, state-format migrations, scheduled self-updates, plugin-marketplace updates and cross-project all-or-nothing upgrades are not implemented in this release.
