"""Durable agenda runner. Agents choose work; the host enforces only effect boundaries."""

from contextlib import contextmanager
import concurrent.futures
import json
import subprocess
import threading
import time
from pathlib import Path

from .adapters import GitHub, command, file_lock
from .store import Conflict, encode, fingerprint, uid
from .worker import codex_workspace_tmpdir, run_worker
from . import clean_integration, proposal_application, repositories
from .verification_artifacts import checkout_identity
from .maintenance import guarded
from . import integration as integration_repair
from . import condition_evidence, verification_identity, verification_logs, verification_outcomes
from .verification_log_view import log_view
from .checkout import require_clean
from .verification_evidence import require_current
from .claim_recovery import recover_expired_claim
from .cancel_execution import reconcile_cancelled
from .verification import VerificationCleanupError, run as run_verification
from .process_barrier import ProcessBarrier, ProcessBarrierError
from .process_inventory import ProcessInventory, launch_identity
from . import managed_workspace, verification_capacity


class Engine:
    def __init__(self, store):
        self.store = store
        self.config = store.config()
        self.root = Path(self.config["repo"])
        self.remote = GitHub(store) if self.config.get("github") else None

    def process_barrier(self, track):
        # Store already owns this durable directory; no recoverable mkdir gap.
        return ProcessBarrier(self.store.path, track)

    def cleanup_blocked(self, track):
        try:
            self.process_barrier(track).require_clear()
        except ProcessBarrierError:
            return True
        return False

    def preserve_cleanup_failure(self, task, error):
        barrier = self.process_barrier(task["track"])
        try:
            barrier.require_clear()
        except ProcessBarrierError:
            # Existing unresolved or corrupt evidence must never be replaced.
            return
        # Compatibility hold for callers not yet recording pre-spawn intent.
        # This is not launch/ownership evidence and cannot authorize signalling.
        execution = uid("unattributed-cleanup")
        evidence = {"task": task["id"], "error": str(error), "origin": "cleanup-failure"}
        revision = barrier.begin(task["attempt"], execution, reason=str(error), evidence=evidence)
        barrier.advance(
            task["attempt"],
            execution,
            "unknown",
            expected_revision=revision,
            reason="Unattributed cleanup failure requires supervisor reconciliation",
            evidence=evidence,
        )

    def check_claim(self, task):
        with self.store.connect() as connection:
            self.store.assert_claim(connection, task)

    def update(self, task, **values):
        allowed = {
            "branch",
            "workspace",
            "issue",
            "pr",
            "head",
            "verification",
            "review",
            "landing",
            "repo_state",
        }
        if not set(values) <= allowed:
            raise ValueError("Unknown track fields")
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            c.execute(
                "UPDATE tracks SET " + ",".join(k + "=?" for k in values) + ",updated=? WHERE id=?",
                (*values.values(), time.time(), task["track"]),
            )

    def ensure_workspace(self, task):
        records = proposal_application.read(self, task["track"])
        # Git worktree registration writes shared .git/config even for different tracks.
        with file_lock(self.store.path / "locks" / "git-metadata.lock", blocking=True):
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
            if repositories.multi(self.config):
                active = {
                    name: record
                    for name, record in records.items()
                    if record and record["phase"] in proposal_application.ACTIVE
                }
                if active:
                    self.process_barrier(task["track"]).require_clear()
                    for record in active.values():
                        proposal_application.require_scope(self, task, record)
                workspaces = self._ensure_repository_workspaces(task)
                primary = repositories.specs(self.config)[0]["name"]
                codex_workspace_tmpdir(
                    workspaces[primary], self.store.path, task["attempt"], create=False
                )
                return workspaces
            if records and records["phase"] in proposal_application.ACTIVE:
                self.process_barrier(task["track"]).require_clear()
                proposal_application.require_scope(self, task, records)
                track = self.store.track(task["track"])
                workspace = Path(track["workspace"] or "").resolve()
                if (
                    not track["workspace"]
                    or not workspace.is_dir()
                    or str(workspace) != records["intent"]["workspace"]
                    or command(["git", "branch", "--show-current"], workspace) != track["branch"]
                ):
                    raise Conflict(
                        "Unfinished proposal checkout ownership changed; preserve evidence"
                    )
                managed = managed_workspace.ensure(self, task, proposal_recovery=True)
                if managed is not None and managed != workspace:
                    raise Conflict("Proposal checkout differs from managed ownership evidence")
                return workspace
            managed = managed_workspace.ensure(self, task)
            workspace = managed if managed is not None else self._ensure_workspace(task)
            codex_workspace_tmpdir(workspace, self.store.path, task["attempt"], create=False)
            return workspace

    def repository_state(self, track, name):
        return repositories.state(track, self.config, name)

    def update_repository_state(self, task, name, *, invalidate=False, **values):
        repositories.update(self, task, name, invalidate=invalidate, **values)

    def _ensure_repository_workspaces(self, task):
        track = self.store.track(task["track"])
        declarations = repositories.specs(self.config)
        workspaces = {}
        worktree_root = Path(self.config.get("worktree_root") or self.store.path / "worktrees")
        worktree_root.mkdir(parents=True, exist_ok=True)
        for index, spec in enumerate(declarations):
            name = spec["name"]
            state = self.repository_state(track, name)
            request_suffix = track["request"].split("-")[-1][:8]
            suffix = "" if index == 0 else "-" + name
            branch = state["branch"] or f"todo/{track['id']}-{request_suffix}{suffix}"
            workspace = (
                Path(state["workspace"])
                if state["workspace"]
                else (worktree_root / f"{track['id']}-{request_suffix}{suffix}")
            )
            if (
                worktree_root.is_symlink()
                or workspace.is_symlink()
                or workspace.parent.resolve() != worktree_root.resolve()
                or not branch.startswith("todo/" + track["id"] + "-")
            ):
                raise Conflict("Repository workspace ownership path or branch is invalid")
            command(["git", "check-ref-format", "--branch", branch], spec["path"])
            if not state["branch"] or not state["workspace"]:
                self.update_repository_state(task, name, branch=branch, workspace=str(workspace))
            if not workspace.exists():
                base_head = repositories.fetch_base(spec["path"], spec["base"])
                exists = (
                    subprocess.run(
                        ["git", "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                        cwd=spec["path"],
                    ).returncode
                    == 0
                )
                args = ["git", "worktree", "add"]
                args += (
                    [str(workspace), branch]
                    if exists
                    else ["-b", branch, str(workspace), base_head]
                )
                command(args, spec["path"])
            actual_branch = command(["git", "branch", "--show-current"], workspace)
            actual_head = command(["git", "rev-parse", "HEAD"], workspace)
            if actual_branch != branch:
                raise Conflict("Repository workspace branch does not match registered ownership")
            if state["head"] and state["head"] != actual_head:
                journal = proposal_application.read(self, track["id"], name)
                intent = journal.get("intent", {}) if journal else {}
                if (
                    not journal
                    or journal["phase"] not in proposal_application.ACTIVE
                    or intent.get("workspace") != str(workspace.resolve())
                    or intent.get("before_head") != state["head"]
                ):
                    raise Conflict("Repository workspace HEAD changed; preserve it for inspection")
                proposal_application.require_scope(self, task, journal)
            else:
                self.update_repository_state(task, name, head=actual_head)
            workspaces[name] = workspace
            track = self.store.track(task["track"])
        return workspaces

    def _ensure_workspace(self, task):
        t = self.store.track(task["track"])
        if not t["branch"]:
            branch = "todo/" + t["id"] + "-" + t["request"].split("-")[-1][:8]
            workspace = str(self.store.path / "worktrees" / branch.replace("/", "-"))
            self.update(task, branch=branch, workspace=workspace)
            t = self.store.track(task["track"])
        workspace = Path(t["workspace"])
        if not workspace.exists():
            base_head = repositories.fetch_base(self.root, self.config["base"])
            exists = (
                subprocess.run(
                    ["git", "show-ref", "--verify", "--quiet", "refs/heads/" + t["branch"]],
                    cwd=self.root,
                ).returncode
                == 0
            )
            args = ["git", "worktree", "add"]
            args += (
                [str(workspace), t["branch"]]
                if exists
                else ["-b", t["branch"], str(workspace), base_head]
            )
            command(args, self.root)
        actual = command(["git", "branch", "--show-current"], workspace)
        if actual != t["branch"]:
            raise Conflict("Workspace branch does not match registered ownership")
        self.update(task, head=command(["git", "rev-parse", "HEAD"], workspace))
        return workspace

    def verify(self, task, workspace, *, scope="full", repo_name=None, persist=True):
        if repositories.multi(self.config) and repo_name is None:
            if not isinstance(workspace, dict):
                raise ValueError("Multi-repository verification requires named workspaces")
            records = {}
            track = self.store.track(task["track"])
            repositories.require_clean(track, self.config, workspace)
            for spec in repositories.specs(self.config):
                records[spec["name"]] = self.verify(
                    task,
                    workspace[spec["name"]],
                    scope=scope,
                    repo_name=spec["name"],
                    persist=False,
                )
            track = self.store.track(task["track"])
            primary = records[repositories.specs(self.config)[0]["name"]]
            passed = all(verification_outcomes.passed(row) for row in records.values())
            outcome = (
                "passed"
                if passed
                else "failed"
                if any(verification_outcomes.outcome(row) == "failed" for row in records.values())
                else "inconclusive"
            )
            aggregate = {
                **primary,
                "repositories": records,
                "heads": {name: row["head"] for name, row in records.items()},
                "ok": passed,
                "outcome": outcome,
                "reason": "All repository verifiers passed"
                if passed
                else "At least one repository verifier did not pass",
                "output": "\n".join(
                    f"[{name}] {row.get('output', '')}" for name, row in records.items()
                )[-12000:],
                "error": None if passed else "RepositoryVerificationFailed",
                "at": max(row["at"] for row in records.values()),
            }
            log = self.store.path / "attempts" / task["attempt"]
            log.mkdir(parents=True, exist_ok=True)
            (log / "verification.json").write_text(encode(aggregate))
            self.update(task, verification=encode(aggregate))
            with self.store.transaction() as c:
                for name, row in records.items():
                    self.store.event(
                        c,
                        "verification.recorded",
                        task["track"],
                        {**row, "repository": name},
                    )
                self.store.event(c, "verification.aggregate-recorded", task["track"], aggregate)
            return aggregate
        config = self.config
        if repositories.multi(self.config):
            spec = next(row for row in repositories.specs(self.config) if row["name"] == repo_name)
            config = {
                **self.config,
                "repo": spec["path"],
                "base": spec["base"],
                "verify": spec["verify"],
                "writable_patterns": spec["writable"],
            }
        proposal_application.require_clear(self, task["track"], task)
        self.process_barrier(task["track"]).require_clear()
        self.check_claim(task)
        if integration_repair.merge_head(workspace) or integration_repair.unmerged(workspace):
            raise Conflict("Verification requires a committed, resolved merge")
        head = require_clean(workspace)
        tree = command(["git", "rev-parse", "HEAD^{tree}"], workspace)
        environment = verification_identity.execution_environment()
        identity = verification_identity.capture(config, workspace, environment, scope=scope)
        scope = identity.get("scope", "full")
        key = fingerprint([tree, config["verify"]])
        prior = self.store.track(task["track"])["verification"]
        if prior:
            prior = json.loads(prior)
            if repositories.multi(self.config):
                prior = prior.get("repositories", {}).get(repo_name)
            if not prior:
                prior = {}
            same_identity = verification_identity.matches(prior.get("identity"), identity)
            if (
                prior.get("key") == key
                and verification_outcomes.passed(prior)
                and prior.get("head") == head
                and prior.get("tree") == tree
                and prior.get("command") == config["verify"]
                and prior.get("scope", "full") == scope
                and same_identity
            ):
                require_clean(workspace, head)
                return prior
        log = self.store.path / "attempts" / task["attempt"]
        if repo_name and repositories.multi(self.config):
            log = log / "verification" / repo_name
        log.mkdir(parents=True, exist_ok=True)
        started = time.time()
        log_reference = None
        checks = []
        outcome, reason = "inconclusive", "Verification did not finish"
        try:
            before = verification_identity.capture(config, workspace, environment, scope=scope)
            if not verification_identity.matches(identity, before):
                raise verification_identity.VerificationIdentityError(
                    "Verification inputs changed before execution"
                )
            commands = [
                (f"preflight-{index}", argv)
                for index, argv in enumerate(identity.get("preflight", []), start=1)
            ]
            if scope == "partial":
                commands.extend(
                    (f"related-{index}", argv)
                    for index, argv in enumerate(identity["policy"]["related"], start=1)
                )
            else:
                commands.append(("final", identity["command"]))
            for stage, argv in commands:
                self.check_claim(task)
                self.process_barrier(task["track"]).require_clear()
                execution = launch_identity(self.store.path, task)
                log_reference = verification_logs.reference(execution)
                check = {
                    "stage": stage,
                    "command": argv,
                    "logReference": log_reference,
                    "ok": False,
                }
                checks.append(check)
                limit = self.config.get("verify_concurrency")
                with verification_capacity.slot(
                    self.store.path,
                    limit,
                    task,
                    execution,
                    stage=stage,
                    workspace=workspace,
                    head=head,
                    scope=scope,
                    check=lambda: self.check_claim(task),
                ):
                    if limit is not None:
                        # Waiting grants capacity only, never claim/input validity.
                        self.check_claim(task)
                        proposal_application.require_clear(self, task["track"], task)
                        self.process_barrier(task["track"]).require_clear()
                        require_clean(workspace, head)
                        current = verification_identity.capture(
                            config, workspace, environment, scope=scope
                        )
                        if not verification_identity.matches(identity, current):
                            raise verification_identity.VerificationIdentityError(
                                "Verification inputs changed while waiting for capacity"
                            )
                    output = run_verification(
                        argv,
                        workspace,
                        timeout=self.config.get("verify_timeout", 180),
                        env=environment,
                        launch_identity=execution,
                        check=lambda: self.check_claim(task),
                    )
                check.update(
                    ok=True,
                    outcome="passed",
                    reason="Command completed successfully",
                    output=output[-12000:],
                    error=None,
                )
            # Success applies only to the recorded scope; effect gates require full.
            ok, error = True, None
            outcome, reason = "passed", "All selected checks passed"
        except (Conflict, ProcessBarrierError):
            # Stale claims and uncertain cleanup cannot become verification results.
            raise
        except (
            RuntimeError,
            OSError,
            subprocess.TimeoutExpired,
            verification_identity.VerificationIdentityError,
        ) as e:
            output, ok, error = str(e), False, type(e).__name__
            outcome = (
                e.outcome
                if isinstance(e, verification_outcomes.VerificationOutcomeError)
                else "inconclusive"
            )
            reason = str(e)
            if checks and not checks[-1]["ok"]:
                checks[-1].update(
                    outcome=outcome, reason=reason, output=output[-12000:], error=error
                )
                output = checks[-1]["stage"] + " failed: " + output[-10000:]
        try:
            after = verification_identity.capture(config, workspace, environment, scope=scope)
            if not verification_identity.matches(identity, after):
                output = (output + "\nVerification inputs changed during execution").strip()
                ok, error = False, "VerificationIdentityError"
                outcome, reason = "inconclusive", "Verification inputs changed during execution"
        except verification_identity.VerificationIdentityError as e:
            output = (output + "\n" + str(e)).strip()
            ok, error = False, type(e).__name__
            outcome, reason = "inconclusive", str(e)
        try:
            require_clean(workspace, head)
        except Conflict as e:
            output = (output + "\n" + str(e)).strip()
            ok, error = False, str(e)
            outcome, reason = "inconclusive", str(e)
        record = {
            "head": head,
            "tree": tree,
            "key": key,
            "command": config["verify"],
            "identity": identity,
            "scope": scope,
            "logReference": log_reference,
            "checks": checks,
            "ok": ok,
            "outcome": outcome,
            "reason": reason,
            "output": output[-12000:],
            "error": error,
            "at": started,
        }
        (log / "verification.json").write_text(encode(record))
        if persist and not repositories.multi(self.config):
            self.update(task, verification=encode(record))
        with self.store.transaction() as c:
            self.store.event(
                c,
                "verification.recorded",
                task["track"],
                {**record, **({"repository": repo_name} if repo_name else {})},
            )
        return record

    def apply_changes(
        self,
        task,
        workspace,
        changes,
        repair=None,
        expected_head=None,
        result=None,
        _batch=None,
    ):
        if repositories.multi(self.config):
            track = self.store.track(task["track"])
            if not isinstance(workspace, dict):
                raise ValueError("Multi-repository changes require named workspaces")
            if repair:
                repo_name = repair.get("repository")
                declarations = {row["name"]: row for row in repositories.specs(self.config)}
                if repo_name not in declarations or not isinstance(
                    repair.get("candidate_heads"), dict
                ):
                    raise Conflict("Multi-repository integration repair evidence is incomplete")
                heads = repositories.head_map(track, self.config)
                if heads != repair["candidate_heads"]:
                    raise Conflict("Multi-repository integration repair candidate set changed")
                grouped = repositories.route_changes(self.config, changes)
                if set(grouped) - {repo_name} or repo_name not in grouped:
                    raise Conflict("Integration repair proposal must target its named repository")
                for name, candidate in heads.items():
                    actual = command(["git", "rev-parse", "HEAD"], workspace[name])
                    if actual != candidate:
                        raise Conflict("Repository candidate changed during integration repair")
                    if name != repo_name:
                        from .checkout import require_clean

                        require_clean(workspace[name], candidate)
                proposal_application.apply(
                    self,
                    task,
                    workspace[repo_name],
                    grouped[repo_name],
                    repair=repair,
                    expected_head=repair["workspace_head"],
                    result=result,
                    repo_name=repo_name,
                    patterns=declarations[repo_name]["writable"],
                )
                return
            heads = repositories.head_map(track, self.config)
            if _batch:
                batch_intent = _batch["intent"]
                if expected_head != batch_intent["aggregate_head"]:
                    raise Conflict("Recovered repository proposal candidate identity changed")
                heads = batch_intent["base_heads"]
                if set(heads) != {row["name"] for row in repositories.specs(self.config)}:
                    raise Conflict("Recovered repository proposal HEAD map is incomplete")
            elif expected_head is not None and expected_head != repositories.fingerprint(heads):
                raise Conflict("Repository candidate changed during worker execution")
            grouped = repositories.route_changes(self.config, changes)
            declarations = {row["name"]: row for row in repositories.specs(self.config)}
            from .change_proposal import prepare_changes

            repositories.require_clean(track, self.config, workspace)
            if _batch and set(grouped) != set(_batch["intent"]["repositories"]):
                raise Conflict("Recovered repository proposal set changed")
            for name, items in grouped.items():
                prior = proposal_application.read(self, task["track"], name)
                actual = command(["git", "rev-parse", "HEAD"], workspace[name])
                if (
                    _batch
                    and prior
                    and prior["phase"] == "committed"
                    and prior["intent"].get("batch_id") == _batch["id"]
                    and prior["intent"]["proposal"] == items
                    and prior["head"] == actual
                ):
                    continue
                if actual != heads[name]:
                    raise Conflict("Repository candidate changed before proposal replay: " + name)
                prepare_changes(workspace[name], items, declarations[name]["writable"], heads[name])
            batch = _batch or proposal_application.begin_batch(
                self, task, heads, grouped, changes, result
            )
            for name in declarations:
                items = grouped.get(name)
                if not items:
                    continue
                proposal_application.apply(
                    self,
                    task,
                    workspace[name],
                    items,
                    expected_head=heads[name],
                    result=result,
                    repo_name=name,
                    patterns=declarations[name]["writable"],
                    batch_id=batch["id"],
                )
            proposal_application.finish_batch(self, task, batch)
            return
        return proposal_application.apply(
            self, task, workspace, changes, repair, expected_head, result
        )

    def publish(self, task, workspace, doc):
        proposal_application.require_clear(self, task["track"], task)
        self.process_barrier(task["track"]).require_clear()
        t = self.store.track(task["track"])
        repositories.require_current(self, t, workspace, t["verification"])
        with file_lock(self.store.path / "locks" / "publish.lock", blocking=True):
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
            t = self.store.track(task["track"])
            repositories.require_current(self, t, workspace, t["verification"])
            if repositories.multi(self.config):
                for spec in repositories.specs(self.config):
                    name = spec["name"]
                    state = self.repository_state(t, name)
                    command(["git", "push", "origin", state["branch"]], workspace[name])
                    remote = self.remote.for_repository(name) if self.remote else None
                    if remote:
                        pr = remote.pr(task, {**t, **state}, doc)
                        self.update_repository_state(task, name, pr=pr["number"])
                        t = self.store.track(task["track"])
            else:
                command(["git", "push", "origin", t["branch"]], workspace)
                if self.remote:
                    pr = self.remote.pr(task, t, doc)
                    self.update(task, pr=pr["number"])

    def context(self, task, workspace):
        t = self.store.track(task["track"])
        snap = self.store.snapshot()
        primary_workspace = (
            workspace[repositories.specs(self.config)[0]["name"]]
            if repositories.multi(self.config)
            else workspace
        )
        doc = json.loads(t["document"])
        revision = self.store.path / "tracks" / t["id"] / "revisions" / f"{t['revision']:06}"
        track_document = (
            revision / "track.html" if "presentation" in doc else revision.with_suffix(".md")
        )
        doc.pop("presentation", None)
        context = {
            "task": {k: task[k] for k in ("id", "kind", "purpose", "attempt")},
            "document": doc,
            "head": t["head"],
            "document_revision": t["revision"],
            "endpoint": self.config["endpoint"],
            "language": self.config.get("language", "en"),
            "worker_protocol": 2,
            "workspace": str(Path(primary_workspace).resolve()),
            "track_document": str(track_document),
            "context_patterns": self.config["context_patterns"],
            "diff": command(
                ["git", "diff", "origin/" + self.config["base"] + "...HEAD"], primary_workspace
            ),
            "writable_patterns": self.config["writable_patterns"],
            "verification": json.loads(t["verification"]) if t["verification"] else None,
            "verification_logs": log_view(
                self.store.path,
                t["id"],
                json.loads(t["verification"]) if t["verification"] else None,
            ),
            "evidence_artifacts": condition_evidence.available(self.store.path, t, self.config),
            "review": condition_evidence.review_view(self.store.path, t, self.config),
            "landing": json.loads(t["landing"]) if t["landing"] else None,
            "recent_results": [
                json.loads(r["body"])
                for r in snap["results"]
                if any(w["id"] == r["task"] and w["track"] == t["id"] for w in snap["tasks"])
            ][-6:],
            "decisions": [d for d in snap["decisions"] if d["track"] == t["id"]],
            "watches": [w for w in snap["watches"] if w["track"] == t["id"]],
            "findings": [f for f in snap["findings"] if f["track"] == t["id"]],
            "incoming_findings": [
                json.loads(f["body"])
                for f in snap["findings"]
                if json.loads(f["body"]).get("target") == t["id"]
            ],
        }
        if repositories.multi(self.config):
            declarations = repositories.specs(self.config)
            repo_context = {}
            diff_parts = []
            for spec in declarations:
                name = spec["name"]
                state = self.repository_state(t, name)
                path = Path(workspace[name]).resolve()
                head = state["head"]
                repo_context[name] = {
                    "path": spec["path"],
                    "base": spec["base"],
                    "branch": state["branch"],
                    "workspace": str(path),
                    "head": head,
                    "writable_patterns": spec["writable"],
                }
                diff_parts.append(
                    f"## {name} ({spec['base']})\n"
                    + command(["git", "diff", "origin/" + spec["base"] + "...HEAD"], path)
                )
            context["workspace"] = repo_context[declarations[0]["name"]]["workspace"]
            context["head"] = repo_context[declarations[0]["name"]]["head"]
            context["aggregate_head"] = repositories.fingerprint(
                {name: row["head"] for name, row in repo_context.items()}
            )
            context["repository_heads"] = {name: row["head"] for name, row in repo_context.items()}
            context["repositories"] = repo_context
            context["diff"] = "\n".join(diff_parts)
        if task["kind"] == "review":
            # Role and attempt come from stored records, never from a result's claims.
            tasks = {w["id"]: w for w in snap["tasks"] if w["track"] == t["id"]}
            records = [
                {
                    "result_id": r["id"],
                    "task_id": r["task"],
                    "kind": tasks[r["task"]]["kind"],
                    "attempt": r["attempt"],
                    "created": r["created"],
                    "body": json.loads(r["body"]),
                }
                for r in sorted(snap["results"], key=lambda r: (r["created"], r["id"]))
                if r["task"] in tasks
            ]
            del context["recent_results"]
            # Implementation activity must not evict earlier independent assessments.
            context["prior_reviews"] = [r for r in records if r["kind"] == "review"]
            context["supplementary_results"] = [r for r in records if r["kind"] != "review"][-6:]
        return context

    def record_review(self, task, result, expected_head=None):
        proposal_application.require_clear(self, task["track"], task)
        self.check_claim(task)
        t = self.store.track(task["track"])
        actual_head = (
            repositories.identity(t, self.config) if repositories.multi(self.config) else t["head"]
        )
        if expected_head is not None and actual_head != expected_head:
            raise Conflict("Review candidate changed during the worker session")
        if repositories.multi(self.config):
            workspaces = {
                spec["name"]: self.repository_state(t, spec["name"])["workspace"]
                for spec in repositories.specs(self.config)
            }
            repositories.require_clean(t, self.config, workspaces)
            repositories.require_current(self, t, workspaces, t["verification"])
        else:
            require_clean(t["workspace"], expected_head or t["head"])
        doc = json.loads(t["document"])
        required = {x["id"] for x in doc["conditions"]}
        rows = result.get("conditions", [])
        if not required <= {x.get("id") for x in rows} or any(
            sum(x.get("id") == id_ for x in rows) != 1 for id_ in required
        ):
            raise ValueError("Reviewer must assess every condition exactly once")
        if any(
            not x.get("evidence") or x.get("verdict") not in ("met", "unmet", "cannot-assess")
            for x in rows
        ):
            raise ValueError("Review condition missing verdict/evidence")
        verdict = result.get("verdict")
        if verdict not in ("met", "unmet", "cannot-assess"):
            raise ValueError("Review verdict required")
        if verdict == "met" and any(x["verdict"] != "met" for x in rows):
            raise ValueError("Contradictory review")
        if verdict == "met":
            if repositories.multi(self.config):
                repositories.require_current(self, t, workspaces, t["verification"])
            else:
                require_current(self.config, t["workspace"], t["head"], t["verification"])
        condition_evidence.require_rows(self.store.path, t, rows, self.config)
        review = {
            "head": t["head"],
            **(
                {"heads": repositories.head_map(t, self.config)}
                if repositories.multi(self.config)
                else {}
            ),
            "documentRevision": t["revision"],
            "attempt": task["attempt"],
            "verdict": verdict,
            "conditions": [x for x in rows if x["id"] in required],
            "additional_assessments": [x for x in rows if x["id"] in required]
            if False
            else [x for x in rows if x["id"] not in required],
            "summary": result["summary"],
        }
        if self.remote and repositories.multi(self.config):
            receipts = {}
            for spec in repositories.specs(self.config):
                name = spec["name"]
                state = self.repository_state(t, name)
                remote = self.remote.for_repository(name)
                if remote and state["pr"]:
                    receipts[name] = remote.post_review(task, {**t, **state}, result)
            if receipts:
                review["receipts"] = receipts
        elif self.remote and t["pr"]:
            review["receipt"] = self.remote.post_review(task, t, result)
        self.update(task, review=encode(review))

    def gate(self, task):
        proposal_application.require_clear(self, task["track"], task)
        self.process_barrier(task["track"]).require_clear()
        t = self.store.track(task["track"])
        if integration_repair.pending(t):
            raise Conflict("Integration repair requires new verification and independent review")
        review = json.loads(t["review"]) if t["review"] else {}
        current_heads = (
            repositories.head_map(t, self.config) if repositories.multi(self.config) else None
        )
        if (
            review.get("verdict") != "met"
            or review.get("head") != t["head"]
            or review.get("documentRevision") != t["revision"]
            or (current_heads is not None and review.get("heads") != current_heads)
        ):
            raise Conflict("Current independent review is missing or not met")
        if repositories.multi(self.config):
            workspaces = {
                spec["name"]: self.repository_state(t, spec["name"])["workspace"]
                for spec in repositories.specs(self.config)
            }
            repositories.require_current(self, t, workspaces, t["verification"])
        else:
            require_current(self.config, t["workspace"], t["head"], t["verification"])
        try:
            condition_evidence.require_rows(
                self.store.path,
                t,
                review.get("conditions", []) + review.get("additional_assessments", []),
                self.config,
            )
        except ValueError as error:
            raise Conflict(str(error)) from error
        return t

    def land(self, task):
        if self.config["endpoint"] != "land" or not self.config["allow_land"]:
            raise Conflict("Landing not authorized in project contract")
        with file_lock(self.store.path / "locks" / "landing.lock", blocking=True):
            repair = integration_repair.pending(self.store.track(task["track"]))
            if repair:
                # A prior attempt may have persisted repair intent before queuing its worker.
                return integration_repair.followup(repair)
            t = self.gate(task)
            if repositories.multi(self.config):
                return self._land_repositories(task, t)
            base_ref = "refs/heads/" + self.config["base"]
            base = repositories.fetch_base(self.root, self.config["base"])
            already = (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", t["head"], base], cwd=self.root
                ).returncode
                == 0
            )
            if already:
                receipt = {"head": t["head"], "baseBefore": base, "merged": base, "recovered": True}
                self.update(task, landing=encode(receipt))
                return {
                    "summary": "Remote already contains this change; recovered landing",
                    "next": [
                        {
                            "kind": "triage",
                            "purpose": "Assess landed code, review residue and findings",
                        }
                    ],
                }
            # One fresh integration checkout per attempt. Recovery preserves previous checkouts.
            integration = self.store.path / "integrations" / task["attempt"]
            with file_lock(self.store.path / "locks/git-metadata.lock", blocking=True):
                command(["git", "worktree", "add", "--detach", str(integration), base], self.root)
                created = checkout_identity(integration)
            try:
                command(["git", "merge", "--no-ff", "--no-edit", t["head"]], integration)
            except RuntimeError as e:
                if not integration_repair.unmerged(integration):
                    raise  # A failed Git command is not necessarily a merge conflict.
                return integration_repair.request_repair(
                    self, task, integration, base, "Integration conflict: " + str(e)
                )
            clean_integration.capture(self.store, t, task, integration, base, created)
            verification = self.verify(task, integration)
            # Combined verification belongs to the integration, not the candidate head.
            combined = verification
            self.update(task, verification=t["verification"])
            if not verification_outcomes.passed(combined):
                if verification_outcomes.outcome(combined) == "inconclusive":
                    detail = combined.get("reason") or combined.get("output", "")
                    return {
                        "summary": "Combined verification inconclusive: " + detail,
                        "question": "Combined verification could not establish a product result. "
                        "Candidate HEAD "
                        + t["head"]
                        + " and its review are preserved. Inspect integration "
                        + str(integration)
                        + " and verification evidence in attempts/"
                        + task["attempt"]
                        + "/verification.json. Which environment/check recovery is authorized "
                        "before retrying landing for this candidate? Cause: " + detail,
                    }
                return integration_repair.request_repair(
                    self,
                    task,
                    integration,
                    base,
                    "Combined verification failed: " + combined["output"],
                )
            merged = command(["git", "rev-parse", "HEAD"], integration)
            intent = {
                "candidate": t["head"],
                "base": base,
                "merged": merged,
                "verification": combined,
                "ref": base_ref,
            }
            key = fingerprint([t["head"], base])
            effect = "landing-" + key
            with self.store.transaction() as c:
                self.store.assert_claim(c, task, allow_paused=False)
                c.execute(
                    "INSERT OR IGNORE INTO effects VALUES(?,?,?,?,NULL,?)",
                    (effect, t["id"], "landing", encode(intent), time.time()),
                )
            # --force-with-lease is used ONLY as a compare-and-swap. Both ancestry checks forbid
            # history rewriting: the exact tested merge includes base and PR head.
            command(["git", "merge-base", "--is-ancestor", base, merged], integration)
            command(["git", "merge-base", "--is-ancestor", t["head"], merged], integration)
            command(
                [
                    "git",
                    "push",
                    "--force-with-lease=" + base_ref + ":" + base,
                    "origin",
                    merged + ":" + base_ref,
                ],
                integration,
            )
            remote_sha = command(["git", "ls-remote", "origin", base_ref], self.root).split()[0]
            if remote_sha != merged:
                raise Conflict("Remote moved after landing; reconcile before completion")
            receipt = {
                "head": t["head"],
                "baseBefore": base,
                "merged": merged,
                "verification": combined,
                "effectId": effect,
            }
            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
                c.execute(
                    "UPDATE effects SET receipt=?,updated=? WHERE id=?",
                    (encode(receipt), time.time(), effect),
                )
            self.update(task, landing=encode(receipt), verification=t["verification"])
            return {
                "summary": "Landed the exact verified integration commit",
                "next": [
                    {"kind": "triage", "purpose": "Assess landed code, review residue and findings"}
                ],
            }

    def _land_repositories(self, task, track):
        declarations = repositories.specs(self.config)
        candidate_heads = repositories.head_map(track, self.config)
        previous = json.loads(track["landing"]) if track["landing"] else {}
        landing = {
            "head": track["head"],
            "heads": candidate_heads,
            "repositories": dict(previous.get("repositories", {})),
            # Receipt bytes remain immutable. Later remote observations live
            # beside them so cleanup can still match the original effect.
            "remoteObservations": dict(previous.get("remoteObservations", {})),
            "status": "in_progress",
        }
        for spec in declarations:
            name, base_name = spec["name"], spec["base"]
            root = Path(spec["path"])
            candidate = candidate_heads[name]
            old = landing["repositories"].get(name)
            base_ref = "refs/heads/" + base_name
            base = repositories.fetch_base(root, base_name)
            if old and old.get("head") == candidate and old.get("merged"):
                command(["git", "merge-base", "--is-ancestor", old["merged"], base], root)
                landing["remoteObservations"][name] = base
                self.update(task, landing=encode(landing))
                continue
            if (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", candidate, base], cwd=root
                ).returncode
                == 0
            ):
                receipt = {
                    "head": candidate,
                    "baseBefore": base,
                    "merged": base,
                    "remoteHead": base,
                    "recovered": True,
                }
                landing["repositories"][name] = receipt
                landing["remoteObservations"][name] = base
                self.update(task, landing=encode(landing))
                continue
            checkout = self.store.path / "integrations" / (task["attempt"] + "-" + name)
            if checkout.exists():
                raise Conflict("Repository integration checkout already exists; preserve it")
            with file_lock(self.store.path / "locks/git-metadata.lock", blocking=True):
                command(["git", "worktree", "add", "--detach", str(checkout), base], root)
            created = checkout_identity(checkout)
            try:
                try:
                    command(["git", "merge", "--no-ff", "--no-edit", candidate], checkout)
                except RuntimeError as error:
                    if integration_repair.unmerged(checkout):
                        return integration_repair.request_repair(
                            self,
                            task,
                            checkout,
                            base,
                            f"Integration conflict in repository {name}: {error}",
                            repo_name=name,
                        )
                    raise
                clean_integration.capture(
                    self.store,
                    track,
                    task,
                    checkout,
                    base,
                    created,
                    candidate_head=candidate,
                    repo_name=name,
                )
                verification = self.verify(
                    task, checkout, scope="full", repo_name=name, persist=False
                )
                if not verification_outcomes.passed(verification):
                    row = {
                        "head": candidate,
                        "baseBefore": base,
                        "verification": verification,
                        "status": verification_outcomes.outcome(verification),
                    }
                    landing["repositories"][name] = row
                    self.update(task, landing=encode(landing))
                    detail = verification.get("reason") or verification.get("output", "")
                    return {
                        "summary": f"Repository integration verification {row['status']}: {name}",
                        "question": (
                            f"Integration verification for {name} did not pass. Candidate {candidate} "
                            f"is preserved. Inspect {checkout} and attempts/{task['attempt']}/verification/{name}. "
                            f"Cause: {detail}"
                        ),
                    }
                merged = command(["git", "rev-parse", "HEAD"], checkout)
                effect = "landing-" + fingerprint([name, candidate, base])
                intent = {
                    "repository": name,
                    "candidate": candidate,
                    "base": base,
                    "merged": merged,
                    "verification": verification,
                    "ref": base_ref,
                }
                with self.store.transaction() as connection:
                    self.store.assert_claim(connection, task, allow_paused=False)
                    connection.execute(
                        "INSERT OR IGNORE INTO effects VALUES(?,?,?,?,NULL,?)",
                        (effect, track["id"], "landing", encode(intent), time.time()),
                    )
                command(["git", "merge-base", "--is-ancestor", base, merged], checkout)
                command(["git", "merge-base", "--is-ancestor", candidate, merged], checkout)
                command(
                    [
                        "git",
                        "push",
                        "--force-with-lease=" + base_ref + ":" + base,
                        "origin",
                        merged + ":" + base_ref,
                    ],
                    checkout,
                )
                remote_sha = command(["git", "ls-remote", "origin", base_ref], root).split()[0]
                if remote_sha != merged:
                    raise Conflict(f"Repository {name} remote moved after landing; reconcile first")
                receipt = {
                    "head": candidate,
                    "baseBefore": base,
                    "merged": merged,
                    "remoteHead": remote_sha,
                    "verification": verification,
                    "effectId": effect,
                }
                with self.store.transaction() as connection:
                    self.store.assert_claim(connection, task)
                    connection.execute(
                        "UPDATE effects SET receipt=?,updated=? WHERE id=?",
                        (encode(receipt), time.time(), effect),
                    )
                landing["repositories"][name] = receipt
                landing["remoteObservations"][name] = remote_sha
                self.update(task, landing=encode(landing))
            finally:
                # Keep integration worktrees as cleanup-owned evidence; never remove on failure.
                pass
        landing["status"] = "complete"
        self.update(task, landing=encode(landing))
        return {
            "summary": "Landed the verified integration commit in every repository",
            "next": [
                {"kind": "triage", "purpose": "Assess landed code, review residue and findings"}
            ],
        }

    def complete(self, task):
        t = self.gate(task)
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            unresolved = c.execute(
                "SELECT id FROM tasks WHERE track=? AND id<>? AND kind<>'complete' "
                "AND status IN ('queued','running','waiting')",
                (t["id"], task["id"]),
            ).fetchall()
            if unresolved:
                return {
                    "summary": "Completion waits for existing obligations",
                    "wait_for": [r["id"] for r in unresolved],
                }
        if self.config["endpoint"] == "land":
            from .triage import cleared, state_key

            with self.store.transaction() as c:
                self.store.assert_claim(c, task)
                if not cleared(self.store, c, t["id"]):
                    work = self.store.enqueue(
                        c,
                        t["id"],
                        "triage",
                        "Assess untriaged landing before completion",
                        "triage-required:" + state_key(self.store, c, t["id"]),
                    )
                    return {
                        "summary": "Completion waits for post-landing triage",
                        "wait_for": [work],
                    }
            receipt = json.loads(t["landing"]) if t["landing"] else {}
            if receipt.get("head") != t["head"]:
                raise Conflict("Landing evidence missing")
            if repositories.multi(self.config):
                heads = repositories.head_map(t, self.config)
                if receipt.get("heads") != heads or receipt.get("status") != "complete":
                    raise Conflict("Every repository landing receipt must match current candidates")
                for spec in repositories.specs(self.config):
                    name, root = spec["name"], Path(spec["path"])
                    row = receipt["repositories"][name]
                    command(["git", "fetch", "origin", spec["base"]], root)
                    command(
                        [
                            "git",
                            "merge-base",
                            "--is-ancestor",
                            row["merged"],
                            "origin/" + spec["base"],
                        ],
                        root,
                    )
            else:
                command(["git", "fetch", "origin", self.config["base"]], self.root)
                command(
                    [
                        "git",
                        "merge-base",
                        "--is-ancestor",
                        t["head"],
                        "origin/" + self.config["base"],
                    ],
                    self.root,
                )
            if self.remote and self.remote.repo:
                issue = t["issue"]
                self.remote.close_issue(task, issue)

        return {
            "summary": "Goal verified and delivered"
            if self.config["endpoint"] == "land"
            else "Requested review endpoint achieved; landing not performed",
            "adopt_completion": True,
        }

    @contextmanager
    def process_attempt(self, task):
        with file_lock(self.store.path / "locks" / (task["track"] + ".lock")):
            with self.store.transaction() as connection:
                self.store.assert_claim(connection, task)
            with ProcessInventory(
                self.store.path, task["track"], task["attempt"], task["id"], task["generation"]
            ).lifecycle():
                yield

    @guarded
    def execute(self, task):
        stop = threading.Event()
        adopted = False

        def pulse():
            while not stop.wait(8):
                try:
                    self.store.heartbeat(task)
                except Conflict:
                    return

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            with self.process_attempt(task):
                self.process_barrier(task["track"]).require_clear()
                workspace = self.ensure_workspace(task)
                recovered = None
                if task["kind"] in ("work", "assess"):
                    recovered = proposal_application.recover(self, task, workspace)
                    if isinstance(recovered, list):
                        recovered = next(
                            (
                                row
                                for row in recovered
                                if (
                                    row["intent"]["task"] == task["id"]
                                    or (row.get("recovered_by") or {}).get("task") == task["id"]
                                )
                                and row["intent"]["result"] is not None
                            ),
                            None,
                        )
                    if recovered and not (
                        recovered["intent"]["task"] == task["id"]
                        or (recovered.get("recovered_by") or {}).get("task") == task["id"]
                    ):
                        recovered = None
                else:
                    proposal_application.require_clear(self, task["track"], task)
                t = self.store.track(task["track"])
                doc = json.loads(t["document"])
                if self.remote and self.remote.repo and not t["issue"]:
                    issue = self.remote.issue(task, doc)
                    self.update(task, issue=issue["number"])
                if (
                    self.remote
                    and self.remote.repo
                    and task["kind"] == "work"
                    and task["purpose"].startswith("Repair original obligations after landing")
                ):
                    self.remote.reopen_issue(task, t["issue"])
                if task["kind"] == "land":
                    result = self.land(task)
                elif task["kind"] == "triage":
                    from .triage import Triage

                    Triage(self).run(task)
                    return
                elif task["kind"] == "complete":
                    result = self.complete(task)
                elif task["kind"] == "verify":
                    v = self.verify(task, workspace)
                    result = verification_outcomes.followup(v)
                    if verification_outcomes.passed(v):
                        self.publish(task, workspace, doc)
                else:
                    repair = (
                        integration_repair.prepare(self, task, workspace)
                        if task["kind"] == "work"
                        else None
                    )
                    context = self.context(task, workspace)
                    if repair:
                        context["integration_repair"] = repair
                    if task["kind"] == "review":
                        repositories.require_current(
                            self,
                            t,
                            workspace,
                            context["verification"],
                        )
                    if recovered:
                        result = dict(
                            recovered["intent"]["result"]
                            or {
                                "summary": "Recovered the exact committed proposal",
                                "next": [
                                    {"kind": "review", "purpose": "Assess recovered proposal"}
                                ],
                            }
                        )
                        result.pop("changes", None)
                        result["verify"] = True
                    else:
                        result = run_worker(
                            self.config,
                            context,
                            task,
                            self.store.path,
                            lambda pid: self.store.heartbeat(task, pid),
                        )
                    self.check_claim(task)
                    if task["kind"] == "review":
                        if repositories.multi(self.config):
                            repositories.require_clean(t, self.config, workspace)
                        else:
                            require_clean(workspace, context["head"])
                    candidate_identity = context.get("aggregate_head", context["head"])
                    if repair and not result.get("question"):
                        if not recovered:
                            self.apply_changes(
                                task,
                                workspace,
                                result.get("changes", []),
                                repair,
                                expected_head=candidate_identity,
                                result=result,
                            )
                        integration_repair.finish_repair(self, task, workspace, repair)
                        result.update(
                            verify=True,
                            publish=True,
                            next=[
                                {
                                    "kind": "review",
                                    "purpose": "Independently review repaired integration",
                                }
                            ],
                        )
                    elif result.get("changes"):
                        self.apply_changes(
                            task,
                            workspace,
                            result["changes"],
                            expected_head=candidate_identity,
                            result=result,
                        )
                    final_requested = bool(
                        result.get("verify")
                        or result.get("publish")
                        or any(
                            row["kind"] in ("review", "land", "complete")
                            for row in (result.get("next") or [])
                        )
                    )
                    if final_requested or result.get("changes"):
                        v = self.verify(
                            task, workspace, scope="full" if final_requested else "partial"
                        )
                        if not verification_outcomes.passed(v):
                            result = verification_outcomes.followup(v)
                        elif result.get("publish"):
                            self.publish(task, workspace, doc)
                    if task["kind"] == "review":
                        self.record_review(task, result, expected_head=candidate_identity)
                    self.record_watches(task, result.get("watches", []))
                adopt = result.pop("adopt_completion", False)
                # Publish task completion and track completion in one transaction. Otherwise
                # reconciliation can schedule new work in the gap and undo a finished repair.
                with self.store.transaction() as c:
                    self.store.finish(task, result, connection=c)
                    if adopt:
                        latest = self.store.track(task["track"], c)
                        if latest["revision"] != task["input_revision"] or latest[
                            "control"
                        ] not in ("active", "paused"):
                            raise Conflict("Completion scope changed")
                        if self.config["endpoint"] == "land":
                            from .triage import cleared

                            if not cleared(self.store, c, task["track"]):
                                raise Conflict("Triage changed before completion adoption")
                        if c.execute(
                            "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
                            (task["track"],),
                        ).fetchone():
                            raise Conflict("New obligations arrived before completion adoption")
                        c.execute(
                            "UPDATE tracks SET status=?,control=?,updated=? WHERE id=?",
                            (
                                "done" if self.config["endpoint"] == "land" else "open",
                                "finished",
                                time.time(),
                                task["track"],
                            ),
                        )
                        self.store.event(
                            c,
                            "completion.adopted",
                            task["track"],
                            {"endpoint": self.config["endpoint"]},
                        )
                        self.store.event(
                            c,
                            "cleanup.requested",
                            task["track"],
                            {
                                "request": latest["request"],
                                "head": latest["head"],
                            },
                        )
                        adopted = True
        except Exception as e:
            self.fail(task, e)
        finally:
            stop.set()
            thread.join(timeout=1)
            # process_attempt has released its lock and sealed the launch inventory.
            reconcile_cancelled(self.store, track=task["track"])
        if adopted:
            self.cleanup_finished(task["track"])

    def cleanup_finished(self, track_id=None):
        if not self.config.get("cleanup_on_complete", True):
            return
        from .cleanup import cleanup_track, receipt_path, read_json

        with self.store.connect() as connection:
            requested = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT track FROM events WHERE type='cleanup.requested'"
                )
            }
            tracks = [
                dict(row)
                for row in connection.execute("SELECT * FROM tracks WHERE control='finished'")
                if row["id"] in requested and (track_id is None or row["id"] == track_id)
            ]
        for track in tracks:
            try:
                if read_json(receipt_path(self.store, track)).get("status") == "complete":
                    continue
                cleanup_track(self.store, track["id"])
            except Exception as error:
                # Cleanup is retryable maintenance; it must not turn delivered work into failure.
                with self.store.transaction() as connection:
                    self.store.event(
                        connection,
                        "cleanup.deferred",
                        track["id"],
                        {
                            "request": track["request"],
                            "reason": str(error),
                        },
                    )

    def record_watches(self, task, rows):
        with self.store.transaction() as c:
            self.store.assert_claim(c, task)
            for row in rows:
                if not all(row.get(k) for k in ("observation", "reason", "trigger", "next_action")):
                    raise ValueError("Watch requires observation, reason, trigger, next_action")
                id_ = fingerprint([task["track"], row["observation"]])[:20]
                c.execute(
                    "INSERT OR IGNORE INTO watches VALUES(?,?,?,?,?,?,?)",
                    (id_, task["track"], encode(row), "open", row["trigger"], None, time.time()),
                )

    def fail(self, task, error):
        if isinstance(error, VerificationCleanupError):
            # Persist the hold before creating an answerable recovery decision.
            # If persistence fails, leave the claim unresolved and propagate.
            self.preserve_cleanup_failure(task, error)
        try:
            self.store.finish(
                task,
                {
                    "summary": str(error),
                    "question": "Execution needs attention: "
                    + str(error)
                    + ". Provide a recovery instruction to resume.",
                },
            )
        except Conflict:
            # Cancellation/reassignment fenced this attempt; its output remains on disk.
            pass
        with self.store.transaction() as c:
            self.store.event(
                c,
                "attempt.error",
                task["track"],
                {"attemptId": task["attempt"], "error": str(error)},
            )

    def reconcile(self):
        # Recovery owns its own execution lock and durable transaction. Close the
        # snapshot connection before entering it; never nest store transactions.
        with self.store.connect() as c:
            expired = [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM tasks WHERE status='running' AND lease<?", (time.time(),)
                ).fetchall()
            ]
        deferred = []
        blocked_tracks = reconcile_cancelled(self.store)
        for task in expired:
            try:
                recover_expired_claim(self.store, task)
            except (Conflict, ProcessBarrierError) as error:
                blocked_tracks.add(task["track"])
                deferred.append(
                    (
                        task["track"],
                        {
                            "workId": task["id"],
                            "generation": task["generation"],
                            "reason": str(error),
                            "retry": "Reconcile after checking attempt identity, launch evidence "
                            "and the execution lock; do not clear evidence to force recovery",
                        },
                    )
                )
        with self.store.transaction() as c:
            for track, evidence in deferred:
                # Retain a diagnostic even when no unique attempt can own a
                # process journal. Repeated reconciliation must not flood it.
                if not c.execute(
                    "SELECT 1 FROM events WHERE type='claim.recovery_blocked' "
                    "AND track=? AND body=?",
                    (track, encode(evidence)),
                ).fetchone():
                    self.store.event(c, "claim.recovery_blocked", track, evidence)
            c.execute(
                "UPDATE tracks SET control='paused' WHERE control='pause-requested' AND NOT EXISTS "
                "(SELECT 1 FROM tasks WHERE tasks.track=tracks.id AND tasks.status='running')"
            )
            for t in c.execute(
                "SELECT * FROM tracks WHERE control='active' AND status='open'"
            ).fetchall():
                if t["id"] in blocked_tracks or self.cleanup_blocked(t["id"]):
                    continue
                # Semantic coalescing repairs pre-existing duplicate follow-ups too. Keep history.
                for kind in ("review", "land", "triage", "complete", "verify"):
                    pending = c.execute(
                        "SELECT * FROM tasks WHERE track=? AND kind=? AND status IN ('queued','waiting','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,created",
                        (t["id"], kind),
                    ).fetchall()
                    for duplicate in pending[1:]:
                        c.execute(
                            "UPDATE tasks SET status='superseded' WHERE id=? AND status<>'running'",
                            (duplicate["id"],),
                        )
                        c.execute(
                            "UPDATE decisions SET status='superseded',answer=? WHERE task=? AND status='open'",
                            ("Joined " + pending[0]["id"], duplicate["id"]),
                        )
                        self.store.event(
                            c,
                            "work.coalesced",
                            t["id"],
                            {"workId": duplicate["id"], "joined": pending[0]["id"]},
                        )
                # Old completion-only deadlocks are runtime dependency waits, never user decisions.
                for d in c.execute(
                    "SELECT d.* FROM decisions d JOIN tasks w ON w.id=d.task WHERE d.track=? AND d.status='open' AND w.kind='complete' AND w.status='waiting'",
                    (t["id"],),
                ).fetchall():
                    if "Other unfinished obligations remain" in d["question"]:
                        c.execute(
                            "UPDATE decisions SET status='superseded',answer='Runtime dependency reconciliation' WHERE id=?",
                            (d["id"],),
                        )
                        c.execute("UPDATE tasks SET status='queued' WHERE id=?", (d["task"],))
                for waiting in c.execute(
                    "SELECT DISTINCT w.work FROM waits w JOIN tasks t ON t.id=w.work WHERE t.track=? AND t.status='waiting'",
                    (t["id"],),
                ).fetchall():
                    blocked = c.execute(
                        "SELECT 1 FROM waits d JOIN tasks t ON t.id=d.dependency WHERE d.work=? AND t.status NOT IN ('done','superseded','cancelled')",
                        (waiting["work"],),
                    ).fetchone()
                    if not blocked:
                        c.execute("DELETE FROM waits WHERE work=?", (waiting["work"],))
                        c.execute("UPDATE tasks SET status='queued' WHERE id=?", (waiting["work"],))
                busy = c.execute(
                    "SELECT 1 FROM tasks WHERE track=? AND status IN ('queued','running','waiting')",
                    (t["id"],),
                ).fetchone()
                if busy:
                    continue
                last = c.execute(
                    "SELECT r.body FROM results r JOIN tasks w ON w.id=r.task "
                    "WHERE w.track=? ORDER BY r.created DESC LIMIT 1",
                    (t["id"],),
                ).fetchone()
                key = fingerprint(
                    [
                        t["request"],
                        t["revision"],
                        t["head"],
                        t["review"],
                        t["landing"],
                        last[0] if last else None,
                    ]
                )
                existing = c.execute(
                    "SELECT 1 FROM tasks WHERE dedup=?", ("assess:" + key,)
                ).fetchone()
                if existing:
                    # No new facts: ask a concrete recovery question instead of spinning models.
                    id_ = uid("decision")
                    w = self.store.enqueue(
                        c, t["id"], "assess", "No new progress; clarify remaining work", id_
                    )
                    c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (w,))
                    c.execute(
                        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                        (
                            id_,
                            t["id"],
                            w,
                            "No new facts or useful follow-up. What should change?",
                            "open",
                            t["revision"],
                            None,
                            time.time(),
                        ),
                    )
                else:
                    self.store.enqueue(
                        c,
                        t["id"],
                        "assess",
                        "Reconcile remaining obligations and choose useful work",
                        "assess:" + key,
                    )

    @guarded
    def run(self, jobs=2, max_tasks=100, daemon=False):
        self.cleanup_finished()
        owner = uid("driver")
        count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            running = set()
            while count < max_tasks or running:
                self.reconcile()
                while len(running) < jobs and count < max_tasks:
                    task = self.store.claim(owner)
                    if not task:
                        break
                    running.add(pool.submit(self.execute, task))
                    count += 1
                    print(
                        encode(
                            {"claimed": task["id"], "track": task["track"], "kind": task["kind"]}
                        ),
                        flush=True,
                    )
                done = {f for f in running if f.done()}
                for f in done:
                    f.result()
                running -= done
                if not running:
                    snap = self.store.snapshot()
                    active = {t["id"] for t in snap["tracks"] if t["control"] == "active"}
                    pending = any(
                        w["status"] == "queued" and w["track"] in active for w in snap["tasks"]
                    )
                    if pending and count < max_tasks:
                        continue
                    if not daemon or count >= max_tasks:
                        break
                time.sleep(0.25 if running else 2)
        return count
