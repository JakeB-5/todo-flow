"""Acceptance tests for tracks spanning multiple Git repositories."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import test_flow
from todo_flow.adapters import command
from todo_flow.change_proposal import prepare_changes
from todo_flow.cli import initialize, main, parser
from todo_flow.engine import Engine
from todo_flow.release import check_config
from todo_flow import repositories
from todo_flow.store import Conflict, Store, encode
from todo_flow.triage import Triage
from todo_flow.cleanup import receipt_path
from todo_flow.cleanup import owned_path
from todo_flow import clean_integration
from todo_flow import proposal_application
from todo_flow import condition_evidence
from todo_flow.delivery_display import delivery_summary, landing_matches

EXAMPLE_CORE_REPO = "core-service"
EXAMPLE_REPORTING_REPO = "reporting-service"


class MultiRepositoryTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def test_fetch_base_does_not_depend_on_shared_fetch_head(self):
        expected = command(["git", "rev-parse", "origin/main^{commit}"], self.repo)
        from todo_flow import adapters

        original = adapters.command
        fetch_head = Path(command(["git", "rev-parse", "--git-path", "FETCH_HEAD"], self.repo))
        if not fetch_head.is_absolute():
            fetch_head = self.repo / fetch_head

        def replace_fetch_head(argv, cwd=None, **kwargs):
            result = original(argv, cwd, **kwargs)
            if argv == ["git", "fetch", "origin", "main"]:
                fetch_head.unlink(missing_ok=True)
            return result

        with patch("todo_flow.adapters.command", side_effect=replace_fetch_head):
            self.assertEqual(repositories.fetch_base(self.repo, "main"), expected)

    def configure_repositories(
        self, *, second_verify=None, endpoint="review", github=None, worktree_root=None
    ):
        self.second_remote = self.root / "service.git"
        command(["git", "init", "--bare", str(self.second_remote)])
        self.second_repo = self.root / "service"
        command(["git", "init", "-b", "main", str(self.second_repo)])
        command(["git", "config", "user.email", "test@example.invalid"], self.second_repo)
        command(["git", "config", "user.name", "Flow Test"], self.second_repo)
        (self.second_repo / "calc.py").write_text("def multiply(a, b):\\n    return a * b\\n")
        command(["git", "add", "."], self.second_repo)
        command(["git", "commit", "-m", "Initial service fixture"], self.second_repo)
        command(["git", "remote", "add", "origin", str(self.second_remote)], self.second_repo)
        command(["git", "push", "-u", "origin", "main"], self.second_repo)

        config = self.s.config()
        config.update(
            repo=str(self.repo),
            base="main",
            verify=[sys.executable, "-c", "raise SystemExit(0)"],
            writable_patterns=["*.py"],
            context_patterns=["*.py"],
            github=github,
            repos=[
                {
                    "name": "core",
                    "path": str(self.repo),
                    "base": "main",
                    "writable": ["*.py"],
                    "verify": [sys.executable, "-c", "raise SystemExit(0)"],
                },
                {
                    "name": "service",
                    "path": str(self.second_repo),
                    "base": "main",
                    "writable": ["*.py"],
                    "verify": second_verify or [sys.executable, "-c", "raise SystemExit(0)"],
                },
            ],
            endpoint=endpoint,
            allow_land=endpoint == "land",
            worktree_root=worktree_root,
        )
        self.s = Store(self.root / "multi-state")
        self.s.configure(config)
        self.s.register(test_flow.DOC)
        self.engine = Engine(self.s)

    def start_work(self):
        self.s.start("addition")
        task = self.s.claim("multi-repo-worker")
        return task, self.engine.ensure_workspace(task)

    def test_init_accepts_repositories_without_redundant_single_repo_flags(self):
        self.configure_repositories()
        state = self.root / "init-state"
        argv = [
            "--state",
            str(state),
            "init",
            "--repos",
            json.dumps(self.s.config()["repos"]),
            "--worktree-root",
            str(self.root / "isolated-worktrees"),
            "--worker-command",
            '["python", "-c", "pass"]',
            "--language",
            "en",
        ]
        output = StringIO()
        with redirect_stdout(output):
            initialize(parser().parse_args(argv))
        stored = json.loads(output.getvalue())
        self.assertEqual([row["name"] for row in stored["config"]["repos"]], ["core", "service"])
        self.assertEqual(stored["config"]["repo"], str(self.repo.resolve()))
        self.assertEqual(stored["config"]["worktree_root"], str(self.root / "isolated-worktrees"))

    def test_engine_uses_configured_worktree_root_and_cleanup_owns_it(self):
        root = self.root / "lane-tmp-worktrees"
        self.configure_repositories(worktree_root=str(root))

        _, workspaces = self.start_work()

        self.assertEqual({Path(path).parent for path in workspaces.values()}, {root})
        config = self.s.config()
        with self.s.transaction():
            self.assertTrue(all(owned_path(self.s, path, config) for path in workspaces.values()))
        self.assertTrue(all(Path(path).is_dir() for path in workspaces.values()))

    def test_worktree_root_rejects_relative_path_and_single_repository_config(self):
        self.configure_repositories()
        config = self.s.config()
        config["worktree_root"] = "relative/worktrees"
        with self.assertRaisesRegex(ValueError, "worktree_root"):
            check_config(config)

        config = self.s.config()
        config.pop("repos")
        config["worktree_root"] = str(self.root / "worktrees")
        with self.assertRaisesRegex(ValueError, "worktree_root"):
            check_config(config)

    def test_init_requires_command_adapter_for_multi_repository_routing(self):
        self.configure_repositories()
        state = self.root / "missing-adapter-state"
        args = parser().parse_args(
            [
                "--state",
                str(state),
                "init",
                "--repos",
                json.dumps(self.s.config()["repos"]),
            ]
        )
        with self.assertRaisesRegex(ValueError, "--worker-command"):
            initialize(args)
        self.assertFalse(state.exists())

    def test_doctor_checks_each_repository_and_uses_its_github_mapping(self):
        self.configure_repositories(github={"core": "example/core", "service": "example/service"})
        calls = []

        def command(argv, cwd=None, **kwargs):
            calls.append((argv, cwd))
            if argv[:3] == ["gh", "repo", "view"]:
                return argv[3]
            return "checked"

        output = StringIO()
        with patch("todo_flow.cli.command", side_effect=command), redirect_stdout(output):
            main(["--state", str(self.s.path), "doctor"])
        result = json.loads(output.getvalue())
        self.assertEqual(
            result,
            {
                "repositories": {
                    "core": {"git": "checked", "origin": "checked", "github": "example/core"},
                    "service": {
                        "git": "checked",
                        "origin": "checked",
                        "github": "example/service",
                    },
                }
            },
        )
        self.assertEqual(
            [cwd for argv, cwd in calls if argv[0] == "git"],
            [str(self.repo)] * 2 + [str(self.second_repo)] * 2,
        )
        self.assertEqual(
            [argv[3] for argv, _ in calls if argv[:3] == ["gh", "repo", "view"]],
            ["example/core", "example/service"],
        )

    def test_legacy_github_string_keeps_primary_pull_request_in_triage(self):
        self.configure_repositories(github="example/core")
        task, _ = self.start_work()
        self.engine.update(task, pr=42)
        self.engine.update_repository_state(task, "service", pr=43)
        self.assertEqual(repositories.github_repo(self.engine.config, "core"), "example/core")
        self.assertIsNone(repositories.github_repo(self.engine.config, "service"))

        class Remote:
            @staticmethod
            def for_repository(_name):
                return object()

        self.engine.remote = Remote()
        with patch("todo_flow.triage.command", return_value=encode([[]])) as request:
            self.assertEqual(Triage(self.engine).remote_sources(self.s.track("addition")), [])

        paths = [call.args[0][-1] for call in request.call_args_list]
        self.assertEqual(
            paths,
            [
                "repos/example/core/pulls/42/reviews?per_page=100",
                "repos/example/core/pulls/42/comments?per_page=100",
                "repos/example/core/issues/42/comments?per_page=100",
            ],
        )

    def test_landing_retry_keeps_effect_receipt_and_separates_latest_remote_head(self):
        self.configure_repositories(endpoint="land")
        task, _ = self.start_work()
        track = self.s.track("addition")
        heads = repositories.head_map(track, self.engine.config)
        candidate = heads["core"]
        effect_id = "landing-original-receipt"
        receipt = {
            "head": candidate,
            "baseBefore": candidate,
            "merged": candidate,
            "remoteHead": candidate,
            "verification": {"ok": True},
            "effectId": effect_id,
        }
        initial = {
            "head": track["head"],
            "heads": heads,
            "repositories": {"core": receipt},
            "status": "in_progress",
        }
        self.engine.update(task, landing=encode(initial))
        with self.s.transaction() as connection:
            connection.execute(
                "INSERT INTO effects VALUES(?,?,?,?,?,?)",
                (
                    effect_id,
                    "addition",
                    "landing",
                    encode({"repository": "core", "candidate": candidate}),
                    encode(receipt),
                    time.time(),
                ),
            )

        def fail_after_core(core_head):
            def fetch(root, _base):
                if Path(root).resolve() == self.repo.resolve():
                    return core_head
                raise RuntimeError("simulated second repository landing failure")

            return fetch

        with patch(
            "todo_flow.engine.repositories.fetch_base", side_effect=fail_after_core(candidate)
        ):
            with self.assertRaisesRegex(RuntimeError, "second repository"):
                self.engine._land_repositories(task, self.s.track("addition"))
        after_failure = json.loads(self.s.track("addition")["landing"])
        self.assertEqual(after_failure["repositories"]["core"], receipt)
        self.assertEqual(after_failure["remoteObservations"]["core"], candidate)

        command(["git", "commit", "--allow-empty", "-m", "Advance core base"], self.repo)
        command(["git", "push", "origin", "main"], self.repo)
        advanced = command(["git", "rev-parse", "HEAD"], self.repo)

        with patch(
            "todo_flow.engine.repositories.fetch_base", side_effect=fail_after_core(advanced)
        ):
            with self.assertRaisesRegex(RuntimeError, "second repository"):
                self.engine._land_repositories(task, self.s.track("addition"))

        after_retry = json.loads(self.s.track("addition")["landing"])
        self.assertEqual(after_retry["repositories"]["core"], receipt)
        self.assertEqual(after_retry["remoteObservations"]["core"], advanced)
        with self.s.connect() as connection:
            effect = connection.execute(
                "SELECT receipt FROM effects WHERE id=?", (effect_id,)
            ).fetchone()
        self.assertEqual(json.loads(effect["receipt"]), receipt)

    def test_engine_creates_one_owned_worktree_per_repository(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        self.assertIsInstance(workspaces, dict)
        self.assertEqual(set(workspaces), {"core", "service"})
        context = self.engine.context(task, workspaces)
        for spec in self.s.config()["repos"]:
            workspace = Path(workspaces[spec["name"]])
            self.assertTrue(workspace.is_dir())
            self.assertEqual(
                command(["git", "branch", "--show-current"], workspace),
                context["repositories"][spec["name"]]["branch"],
            )
            self.assertEqual(
                command(["git", "rev-parse", "HEAD"], workspace),
                context["repositories"][spec["name"]]["head"],
            )
        self.assertEqual(task["track"], "addition")

    def test_concurrent_tracks_get_distinct_worktrees_in_each_repository(self):
        self.configure_repositories()
        self.s.register({**test_flow.DOC, "id": "other", "title": "Other track"})
        self.s.start("addition", "request-addition")
        self.s.start("other", "request-other")
        tasks = [self.s.claim("concurrent-worker-a"), self.s.claim("concurrent-worker-b")]
        self.assertEqual({task["track"] for task in tasks}, {"addition", "other"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.engine.ensure_workspace, tasks))
        self.assertTrue(all(set(workspaces) == {"core", "service"} for workspaces in results))
        for name in ("core", "service"):
            self.assertEqual(len({str(row[name]) for row in results}), 2)
            self.assertEqual(
                len({command(["git", "branch", "--show-current"], row[name]) for row in results}), 2
            )

    def test_candidate_generation_change_rejects_old_aggregate_head(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        context = self.engine.context(task, workspaces)
        command(
            ["git", "commit", "--allow-empty", "-m", "External generation change"],
            workspaces["service"],
        )
        with self.assertRaises(Conflict):
            self.engine.apply_changes(
                task,
                workspaces,
                [{"path": "service:calc.py", "content": "changed"}],
                expected_head=context["aggregate_head"],
            )
        self.assertEqual(
            command(["git", "rev-parse", "HEAD"], workspaces["core"]),
            context["repository_heads"]["core"],
        )

    def test_partial_repository_commit_recovers_batch_before_worker_replay(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        before = self.engine.context(task, workspaces)
        changes = [
            {"path": "core:calc.py", "content": "def add(a, b):\n    return a + b\n"},
            {"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 2\n"},
        ]
        apply = proposal_application.apply

        def fail_second_repository(*args, **kwargs):
            if kwargs.get("repo_name") == "service":
                raise RuntimeError("injected second-repository interruption")
            return apply(*args, **kwargs)

        with patch("todo_flow.proposal_application.apply", side_effect=fail_second_repository):
            with self.assertRaisesRegex(RuntimeError, "second-repository interruption"):
                self.engine.apply_changes(
                    task,
                    workspaces,
                    changes,
                    expected_head=before["aggregate_head"],
                    result={"summary": "two-repository change"},
                )
        batch = proposal_application.read_batch(self.engine, "addition")
        self.assertEqual(batch["phase"], "applying")
        proposal_application.recover(self.engine, task, workspaces)
        batch = proposal_application.read_batch(self.engine, "addition")
        self.assertEqual(batch["phase"], "committed")
        self.assertIn("return a + b", (Path(workspaces["core"]) / "calc.py").read_text())
        self.assertIn("return a * b + 2", (Path(workspaces["service"]) / "calc.py").read_text())

    def test_new_task_recovers_partial_batch_before_require_clear(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        before = self.engine.context(task, workspaces)
        changes = [
            {"path": "core:calc.py", "content": "def add(a, b):\n    return a + b\n"},
            {"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 4\n"},
        ]
        apply = proposal_application.apply

        def fail_second_repository(*args, **kwargs):
            if kwargs.get("repo_name") == "service":
                raise RuntimeError("injected second-repository interruption")
            return apply(*args, **kwargs)

        with patch("todo_flow.proposal_application.apply", side_effect=fail_second_repository):
            with self.assertRaisesRegex(RuntimeError, "second-repository interruption"):
                self.engine.apply_changes(
                    task,
                    workspaces,
                    changes,
                    expected_head=before["aggregate_head"],
                    result={"summary": "two-repository change"},
                )
        self.s.finish(task, {"summary": "Prior task ended after partial proposal"})
        with self.s.transaction() as connection:
            self.s.enqueue(
                connection,
                task["track"],
                "work",
                "Continue after interrupted batch",
                "proposal-batch-successor",
            )
        successor = self.s.claim("proposal-batch-successor")
        self.assertNotEqual(successor["id"], task["id"])

        proposal_application.require_clear(self.engine, task["track"], successor)

        self.assertEqual(
            proposal_application.read_batch(self.engine, task["track"])["phase"], "committed"
        )
        self.assertIn("return a + b", (Path(workspaces["core"]) / "calc.py").read_text())
        self.assertIn("return a * b + 4", (Path(workspaces["service"]) / "calc.py").read_text())

    def test_commit_before_track_adoption_recovers_before_workspace_head_guard(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        context = self.engine.context(task, workspaces)
        changes = [
            {"path": "core:calc.py", "content": "def add(a, b):\n    return a + b\n"},
            {"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 3\n"},
        ]
        adopt = proposal_application.adopt

        def fail_before_adoption(*args, **kwargs):
            if args[-1]["intent"].get("repository") == "core":
                raise RuntimeError("injected adoption interruption")
            return adopt(*args, **kwargs)

        with patch("todo_flow.proposal_application.adopt", side_effect=fail_before_adoption):
            with self.assertRaisesRegex(RuntimeError, "adoption interruption"):
                self.engine.apply_changes(
                    task,
                    workspaces,
                    changes,
                    expected_head=context["aggregate_head"],
                    result={"summary": "two-repository change"},
                )
        core_journal = proposal_application.read(self.engine, "addition", "core")
        self.assertEqual(core_journal["phase"], "ready")
        self.engine.ensure_workspace(task)
        proposal_application.recover(self.engine, task, workspaces)
        self.assertEqual(
            proposal_application.read_batch(self.engine, "addition")["phase"], "committed"
        )
        self.assertIn("return a + b", (Path(workspaces["core"]) / "calc.py").read_text())
        self.assertIn("return a * b + 3", (Path(workspaces["service"]) / "calc.py").read_text())

    def test_named_proposal_changes_only_selected_repository(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        context = self.engine.context(task, workspaces)
        core_before = context["repositories"]["core"]["head"]
        service_before = context["repositories"]["service"]["head"]
        core_file_before = (Path(workspaces["core"]) / "calc.py").read_text()
        self.engine.apply_changes(
            task,
            workspaces,
            [{"path": "service:calc.py", "content": "def multiply(a, b):\\n    return 99\\n"}],
            expected_head=context["aggregate_head"],
        )
        self.assertEqual(
            proposal_application.read_batch(self.engine, "addition")["intent"]["repositories"],
            ["service"],
        )
        self.assertEqual(command(["git", "rev-parse", "HEAD"], workspaces["core"]), core_before)
        self.assertNotEqual(
            command(["git", "rev-parse", "HEAD"], workspaces["service"]), service_before
        )
        self.assertIn("return 99", (Path(workspaces["service"]) / "calc.py").read_text())
        self.assertEqual((Path(workspaces["core"]) / "calc.py").read_text(), core_file_before)

    def test_verification_fails_when_any_repository_verifier_fails(self):
        self.configure_repositories(
            second_verify=[
                sys.executable,
                "-c",
                'import sys; print(\'{"outcome":"failed","reason":"fixture failure"} TODO_FLOW_RESULT_V1\'); sys.exit(7)',
            ]
        )
        task, workspaces = self.start_work()
        result = self.engine.verify(task, workspaces)
        self.assertFalse(result["ok"])
        self.assertEqual(result["repositories"]["core"]["outcome"], "passed")
        self.assertEqual(result["repositories"]["service"]["outcome"], "failed")

    def test_aggregate_verification_wrapper_cannot_drift_from_primary_receipt(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        self.assertTrue(self.engine.verify(task, workspaces)["ok"])
        record = json.loads(self.s.track("addition")["verification"])
        record["tree"] = "0" * 40
        self.engine.update(task, verification=json.dumps(record))
        with self.assertRaises(Conflict):
            repositories.require_current(self.engine, self.s.track("addition"), workspaces, record)

    def test_condition_evidence_reads_all_repository_heads_from_canonical_owner(self):
        self.configure_repositories()
        track = self.s.track("addition")
        primary = "e" * 40
        track["head"] = primary
        secondary = "a" * 40
        track["repo_state"] = encode({"service": {"head": secondary}})
        check = {"ok": True, "logReference": {"track": "addition"}}
        track["verification"] = {
            "ok": True,
            "head": primary,
            "heads": {"core": primary, "service": secondary},
            "repositories": {
                name: {"ok": True, "head": head, "checks": [check]}
                for name, head in (("core", primary), ("service", secondary))
            },
        }

        checks = condition_evidence._checks(track, self.s.config())
        self.assertEqual(
            [(name, head) for name, head, _ in checks], [("core", primary), ("service", secondary)]
        )

        track["repo_state"] = encode({"service": {"head": "b" * 40}})
        self.assertEqual(condition_evidence._checks(track, self.s.config()), [])
        self.assertEqual(condition_evidence._checks(track), [])

    def test_landing_summary_uses_canonical_heads_and_fails_closed(self):
        self.configure_repositories()
        track = self.s.track("addition")
        primary = "e" * 40
        track["head"] = primary
        secondary = "c" * 40
        track["repo_state"] = encode({"service": {"head": secondary}})
        heads = {"core": primary, "service": secondary}
        review = {
            "heads": heads,
            "verdict": "met",
            "head": primary,
            "documentRevision": track["revision"],
        }
        landing = {
            "heads": heads,
            "head": primary,
            "status": "complete",
            "repositories": {
                name: {
                    "head": head,
                    "merged": head,
                    "recovered": True,
                    "baseBefore": head,
                }
                for name, head in heads.items()
            },
        }
        config = self.s.config()
        self.assertTrue(landing_matches(None, track, review, landing, config))
        self.assertFalse(landing_matches(None, track, review, landing, None))

        track["repo_state"] = encode({"service": {"head": "d" * 40}})
        self.assertFalse(landing_matches(None, track, review, landing, config))
        track["repo_state"] = encode({"service": []})
        self.assertFalse(landing_matches(None, track, review, landing, config))

    def test_publish_pushes_each_repository_branch(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        self.engine.apply_changes(
            task,
            workspaces,
            [{"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 1\n"}],
        )
        self.assertTrue(self.engine.verify(task, workspaces)["ok"])
        self.engine.publish(task, workspaces, json.loads(self.s.track("addition")["document"]))
        track = self.s.track("addition")
        for spec in self.s.config()["repos"]:
            state = self.engine.repository_state(track, spec["name"])
            remote_head = command(
                ["git", "ls-remote", "origin", "refs/heads/" + state["branch"]],
                spec["path"],
            ).split()[0]
            self.assertEqual(remote_head, state["head"])

    def test_land_merges_and_verifies_each_repository(self):
        self.configure_repositories(endpoint="land")
        task, workspaces = self.start_work()
        self.engine.apply_changes(
            task,
            workspaces,
            [{"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 1\n"}],
        )
        self.assertTrue(self.engine.verify(task, workspaces)["ok"])
        context = self.engine.context(task, workspaces)
        artifacts = context["evidence_artifacts"]
        self.assertEqual({row["repository"] for row in artifacts}, {"core", "service"})
        service_artifact = next(row for row in artifacts if row["repository"] == "service")
        self.engine.record_review(
            task,
            {
                "summary": "Every repository meets the selected condition",
                "verdict": "met",
                "conditions": [
                    {
                        "id": "sum",
                        "verdict": "met",
                        "evidence": "Reviewed source and service verifier result",
                        "evidenceRefs": [{**service_artifact, "conditionId": "sum"}],
                    }
                ],
            },
            expected_head=context["aggregate_head"],
        )
        review = json.loads(self.s.track("addition")["review"])
        self.assertEqual(
            condition_evidence.inspect_row(
                self.s.path, self.s.track("addition"), review["conditions"][0], self.s.config()
            )["status"],
            "integrity-checked",
        )
        result = self.engine.land(task)
        self.assertIn("every repository", result["summary"])
        landing = json.loads(self.s.track("addition")["landing"])
        self.assertEqual(landing["status"], "complete")
        self.assertEqual(set(landing["repositories"]), {"core", "service"})
        service = landing["repositories"]["service"]
        self.assertTrue(service["verification"]["ok"])
        service_integration = self.s.path / "integrations" / (task["attempt"] + "-service")
        creation = json.loads(
            clean_integration.creation_path(self.s, service_integration).read_text()
        )
        self.assertEqual(creation["repository"], "service")
        track_after_landing = self.s.track("addition")
        with self.s.connect() as connection:
            self.assertIn(
                self.engine.repository_state(track_after_landing, "service")["head"],
                clean_integration.candidates(
                    connection, track_after_landing, creation["tree"], "service"
                ),
            )
        remote_code = command(["git", "--git-dir", str(self.second_remote), "show", "main:calc.py"])
        self.assertIn("return a * b + 1", remote_code)
        self.s.finish(task, {"summary": "landed candidate"})
        with self.s.transaction() as connection:
            self.s.enqueue(
                connection, "addition", "triage", "Review landed multi-repo changes", "triage-multi"
            )
        triage_task = self.s.claim("multi-repo-triage")
        with patch(
            "todo_flow.triage.run_worker",
            return_value={"summary": "No findings remain", "triage": []},
        ):
            Triage(self.engine).run(triage_task)
        complete = self.s.claim("multi-repo-complete")
        self.engine.execute(complete)
        track = self.s.track("addition")
        self.assertEqual(track["control"], "finished")
        cleanup = json.loads(receipt_path(self.s, track).read_text())
        self.assertEqual(cleanup["status"], "complete")
        removed = {row["path"] for row in cleanup["worktrees"] if row["status"] == "removed"}
        self.assertIn(str(Path(workspaces["core"])), removed)
        self.assertIn(str(Path(workspaces["service"])), removed)
        self.assertFalse(Path(workspaces["core"]).exists())
        self.assertFalse(Path(workspaces["service"]).exists())
        with self.s.connect() as connection:
            self.assertEqual(delivery_summary(self.s, connection, "addition")["phase"], "complete")

    def test_conflict_in_one_repository_repairs_only_that_candidate_and_reverifies_all(self):
        self.configure_repositories(endpoint="land")
        task, workspaces = self.start_work()
        self.engine.apply_changes(
            task,
            workspaces,
            [{"path": "service:calc.py", "content": "def multiply(a, b):\n    return a * b + 1\n"}],
        )
        self.assertTrue(self.engine.verify(task, workspaces)["ok"])
        context = self.engine.context(task, workspaces)
        self.engine.record_review(
            task,
            {
                "summary": "Both candidates reviewed",
                "verdict": "met",
                "conditions": [
                    {"id": "sum", "verdict": "met", "evidence": "Both repositories reviewed"}
                ],
            },
            expected_head=context["aggregate_head"],
        )
        candidate = self.engine.repository_state(self.s.track("addition"), "service")["head"]
        (self.second_repo / "calc.py").write_text("def multiply(a, b):\n    return a * b + 2\n")
        command(["git", "add", "calc.py"], self.second_repo)
        command(["git", "commit", "-m", "Advance service base"], self.second_repo)
        command(["git", "push", "origin", "main"], self.second_repo)

        landed = self.engine.land(task)
        self.assertEqual(landed["next"][0]["kind"], "work")
        pending = json.loads(self.s.track("addition")["landing"])
        self.assertEqual(pending["repository"], "service")
        self.s.finish(task, {"summary": "Conflict identified"})
        with self.s.transaction() as connection:
            self.s.enqueue(
                connection,
                "addition",
                "work",
                "Repair service integration conflict",
                "repair-service-conflict",
            )
        repair_task = self.s.claim("repair-worker")
        with patch(
            "todo_flow.engine.run_worker",
            return_value={
                "summary": "Preserved upstream change and candidate behavior",
                "changes": [
                    {
                        "path": "service:calc.py",
                        "content": "def multiply(a, b):\n    return a * b + 2 + 1\n",
                    }
                ],
                "verify": True,
                "publish": True,
                "next": [{"kind": "review", "purpose": "Review repaired candidates"}],
            },
        ):
            self.engine.execute(repair_task)

        track = self.s.track("addition")
        state = self.engine.repository_state(track, "service")
        parents = command(
            ["git", "show", "-s", "--format=%P", "HEAD"], workspaces["service"]
        ).split()
        self.assertEqual(parents, [candidate, pending["base"]])
        self.assertIn("return a * b + 2 + 1", (Path(workspaces["service"]) / "calc.py").read_text())
        verification = json.loads(track["verification"])
        self.assertEqual(verification["heads"]["service"], state["head"])
        self.assertTrue(verification["ok"])
        self.assertIsNone(track["review"])

        review_task = self.s.claim("multi-repo-review")
        with patch(
            "todo_flow.engine.run_worker",
            return_value={
                "summary": "Both repaired candidates meet the condition",
                "verdict": "met",
                "conditions": [
                    {"id": "sum", "verdict": "met", "evidence": "Both candidates rechecked"}
                ],
                "next": [{"kind": "land", "purpose": "Land reviewed candidates"}],
            },
        ):
            self.engine.execute(review_task)
        land_task = self.s.claim("multi-repo-land")
        self.engine.execute(land_task)
        triage_task = self.s.claim("multi-repo-final-triage")
        with patch(
            "todo_flow.triage.run_worker",
            return_value={"summary": "No findings remain", "triage": []},
        ):
            Triage(self.engine).run(triage_task)
        complete_task = self.s.claim("multi-repo-final-complete")
        self.engine.execute(complete_task)
        final_track = self.s.track("addition")
        self.assertEqual(final_track["control"], "finished")
        cleanup = json.loads(receipt_path(self.s, final_track).read_text())
        self.assertEqual(cleanup["status"], "complete")
        self.assertFalse(any(row.get("status") == "preserved" for row in cleanup["worktrees"]))

    def test_route_and_path_boundary_rejects_adversarial_inputs_without_writes(self):
        self.configure_repositories()
        task, workspaces = self.start_work()
        config = self.s.config()
        service = Path(workspaces["service"])
        head_before = command(["git", "rev-parse", "HEAD"], service)
        routed_bad = [
            {},
            {"path": None},
            {"path": 3},
            {"path": "calc.py"},
            {"path": ":calc.py"},
            {"path": "service:"},
            {"path": "Service:calc.py"},
            {"path": "service2:calc.py"},
            {"path": "serv\u0456ce:calc.py"},
            {"path": "service:calc.py:other.py"},
            {"path": "service:/tmp/calc.py"},
            {"path": "service:C:\\Windows\\secret.py"},
            {"path": "unknown:../core/calc.py"},
        ]
        self.assertGreaterEqual(len(routed_bad), 12)
        for change in routed_bad:
            with self.subTest(change=change), self.assertRaises((ValueError, Conflict)):
                repositories.route_changes(config, [change])

        path_bad = [
            "../calc.py",
            "./calc.py",
            "calc.py/../escape.py",
            "folder//calc.py",
            "folder\\calc.py",
            "calc.py:alias.py",
            "cafe\u0301.py",
            ".git/config.py",
            "README.md",
            "nested/../../outside.py",
        ]
        self.assertGreaterEqual(len(path_bad), 10)
        for name in path_bad:
            with self.subTest(path=name), self.assertRaises(Conflict):
                prepare_changes(service, [{"path": name, "content": "bad"}], ["*.py"], head_before)
        with self.subTest(path="repository-writable-scope"), self.assertRaises(Conflict):
            prepare_changes(
                service,
                [{"path": "README.md", "content": "bad"}],
                config["repos"][1]["writable"],
                head_before,
            )
        with self.subTest(path="case-aliases"), self.assertRaises(Conflict):
            prepare_changes(
                service,
                [
                    {"path": "calc.py", "content": "first"},
                    {"path": "CALC.py", "content": "second"},
                ],
                ["*.py"],
                head_before,
            )
        with self.subTest(path="ancestor-descendant"), self.assertRaises(Conflict):
            prepare_changes(
                service,
                [
                    {"path": "nested", "content": "first"},
                    {"path": "nested/child.py", "content": "second"},
                ],
                ["*"],
                head_before,
            )
        outside = self.root / "outside.py"
        outside.write_text("preserve")
        link = service / "escape.py"
        parent_link = service / "linked"
        try:
            link.symlink_to(outside)
            parent_link.symlink_to(self.root, target_is_directory=True)
            for name in ("escape.py", "linked/outside.py"):
                with self.subTest(path=name), self.assertRaises(Conflict):
                    prepare_changes(
                        service, [{"path": name, "content": "bad"}], ["*.py"], head_before
                    )
        finally:
            link.unlink(missing_ok=True)
            parent_link.unlink(missing_ok=True)
        self.assertEqual(command(["git", "rev-parse", "HEAD"], service), head_before)
        self.assertEqual(outside.read_text(), "preserve")
        self.assertGreaterEqual(len(routed_bad) + len(path_bad) + 4, 20)


class CodexExecAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-adapter-tests-")
        self.root = Path(self.temporary.name)
        self.adapter = Path(__file__).resolve().parents[1] / "examples" / "codex_exec_adapter.py"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.workspaces = {}
        self.repositories = {}
        for name in (EXAMPLE_CORE_REPO, EXAMPLE_REPORTING_REPO):
            repo = self.root / f"{name}-repo"
            workspace = self.root / f"{name}-workspace"
            repo.mkdir()
            workspace.mkdir()
            self.workspaces[name] = workspace
            self.repositories[name] = {
                "path": str(repo),
                "base": "main",
                "branch": f"todo/test-{name}",
                "workspace": str(workspace),
                "head": "a" * 40 if name == EXAMPLE_CORE_REPO else "b" * 40,
                "writable_patterns": ["*.md"],
            }
        self.heads = {name: row["head"] for name, row in self.repositories.items()}
        self.fake_codex = self.bin / "codex"
        self.fake_codex.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
prompt = sys.stdin.read()
capture = os.environ.get("FAKE_CODEX_CAPTURE")
if capture:
    Path(capture).write_text(
        json.dumps({"argv": args, "prompt": prompt, "tmpdir": os.environ.get("TMPDIR")}),
        encoding="utf-8",
    )
time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "0")))
raw = os.environ.get("FAKE_CODEX_RAW") == "1"
response = os.environ.get("FAKE_CODEX_RESPONSE", "{}")
output = Path(args[args.index("--output-last-message") + 1])
schema_path = Path(args[args.index("--output-schema") + 1])
if not raw:
    try:
        value = json.loads(response)
    except json.JSONDecodeError:
        value = None
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    def fill(value, node):
        if "anyOf" in node:
            options = [item for item in node["anyOf"] if item.get("type") != "null"]
            return value if value is None or not options else fill(value, options[0])
        if node.get("type") == "object" and isinstance(value, dict):
            for key, child in node.get("properties", {}).items():
                if key not in value:
                    value[key] = None
                elif value[key] is not None:
                    value[key] = fill(value[key], child)
        elif node.get("type") == "array" and isinstance(value, list):
            value = [fill(item, node["items"]) for item in value]
        return value
    if value is not None:
        response = json.dumps(fill(value, schema), ensure_ascii=False)
output.write_text(response, encoding="utf-8")
sys.exit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
""",
            encoding="utf-8",
        )
        self.fake_codex.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def context(self, kind="assess"):
        heads = dict(self.heads)
        return {
            "worker_protocol": 2,
            "task": {"id": "task-1", "kind": kind, "purpose": "Test adapter", "attempt": "a1"},
            "workspace": str(self.workspaces[EXAMPLE_CORE_REPO]),
            "head": heads[EXAMPLE_CORE_REPO],
            "paths": {"document": str(self.root / "document.json")},
            "repositories": dict(self.repositories),
            "repository_heads": heads,
            "aggregate_head": repositories.fingerprint(heads),
            "writable_patterns": ["*.md"],
            "language": "ko",
        }

    def invoke(
        self,
        context,
        response,
        *,
        kind="assess",
        timeout="5",
        raw_response=False,
        sandbox="read-only",
        provide_attempt_tmpdir=True,
    ):
        capture = self.root / f"capture-{uuid.uuid4().hex}.json"
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CODEX_CAPTURE"] = str(capture)
        env["FAKE_CODEX_RESPONSE"] = response if isinstance(response, str) else json.dumps(response)
        env["FAKE_CODEX_RAW"] = "1" if raw_response else "0"
        env["TODO_FLOW_CODEX_SANDBOX"] = sandbox
        if sandbox == "workspace-write" and provide_attempt_tmpdir:
            attempt_tmpdir = self.root / "attempts" / context["task"]["attempt"] / "tmp"
            attempt_tmpdir.mkdir(parents=True, exist_ok=True)
            env["TMPDIR"] = str(attempt_tmpdir)
        elif sandbox == "workspace-write":
            env.pop("TMPDIR", None)
        command = [
            sys.executable,
            str(self.adapter),
            "--model",
            "gpt-6-luna",
            "--effort",
            "max",
            "--timeout",
            timeout,
            "--tmp-dir",
            str(self.root),
        ]
        input_text = (
            context if isinstance(context, str) else json.dumps(context, ensure_ascii=False)
        )
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=False,
        )
        return result, capture

    def test_primary_repository_is_found_by_workspace_not_key_order(self):
        # Stored worker input is written with sorted keys; a secondary repository
        # whose name sorts first must not be taken for the primary.
        context = self.context()
        context["repositories"] = dict(reversed(list(self.repositories.items())))
        text = json.dumps(context, ensure_ascii=False, sort_keys=True)
        result, _ = self.invoke(text, {"summary": "assessment", "next": []})
        self.assertEqual(result.returncode, 0, result.stderr)
        stray = self.root / "unrelated-workspace"
        stray.mkdir()
        context["workspace"] = str(stray)
        result, _ = self.invoke(context, {"summary": "assessment", "next": []})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("aggregate generation changed", result.stderr)

    def test_watch_task_is_accepted_and_cannot_change_files(self):
        result, _ = self.invoke(self.context("watch"), {"summary": "condition not met yet"})
        self.assertEqual(result.returncode, 0, result.stderr)
        change = {"changes": [self.full_change(f"{EXAMPLE_CORE_REPO}:note.md")], "summary": "x"}
        result, _ = self.invoke(self.context("watch"), change)
        self.assertNotEqual(result.returncode, 0)

    def test_question_with_observations_is_normalized_not_rejected(self):
        response = {
            "summary": "need a path",
            "question": "Where is the report?",
            "findings": [{"observation": "missing", "evidence": "rg found nothing"}],
        }
        result, _ = self.invoke(self.context("work"), response)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertNotIn("findings", output)
        self.assertIn("rg found nothing", output["summary"])

    def test_question_with_follow_up_keeps_work_and_carries_question_as_text(self):
        response = {
            "summary": "partial",
            "question": "Confirm the owner?",
            "next": [{"kind": "review", "purpose": "Review the partial result"}],
        }
        result, _ = self.invoke(self.context("work"), response)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertNotIn("question", output)
        self.assertIn("Confirm the owner?", output["summary"])

    def full_change(self, path, content="candidate\n"):
        return {"path": path, "content": content}

    def test_routes_all_four_worker_kinds_and_two_repository_changes(self):
        cases = (
            (
                "assess",
                {
                    "summary": "assessment",
                    "next": [{"kind": "work", "purpose": "Correct verified typo"}],
                },
            ),
            (
                "work",
                {
                    "summary": "two repository proposal",
                    "changes": [
                        self.full_change(f"{EXAMPLE_CORE_REPO}:README.md", "core\n"),
                        self.full_change(f"{EXAMPLE_REPORTING_REPO}:README.md", "sentinel\n"),
                    ],
                },
            ),
            (
                "review",
                {
                    "summary": "reviewed",
                    "verdict": "met",
                    "conditions": [
                        {"id": "c1", "verdict": "met", "evidence": "Observed exact result"}
                    ],
                },
            ),
            ("triage", {"summary": "no findings", "triage": []}),
        )
        for kind, response in cases:
            with self.subTest(kind=kind):
                result, capture = self.invoke(self.context(kind), response, kind=kind)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["summary"], response["summary"])
                call = json.loads(capture.read_text(encoding="utf-8"))
                argv = call["argv"]
                self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-luna")
                self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
                self.assertIn('model_reasoning_effort="max"', argv)
                self.assertIn(str(Path(self.workspaces[EXAMPLE_REPORTING_REPO]).resolve()), argv)
                self.assertIn(EXAMPLE_CORE_REPO, call["prompt"])
                self.assertIn("Never inspect secret material", call["prompt"])
                self.assertIn("shell history", call["prompt"])

    def test_codex_example_uses_workspace_write_and_primary_worktree_tmpdir(self):
        result, capture = self.invoke(
            self.context("work"),
            {"summary": "verified tests", "changes": []},
            kind="work",
            sandbox="workspace-write",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(call["argv"][call["argv"].index("--sandbox") + 1], "workspace-write")
        expected_tmpdir = (self.root / "attempts/a1/tmp").resolve()
        self.assertEqual(Path(call["tmpdir"]).resolve(), expected_tmpdir)
        self.assertTrue(expected_tmpdir.is_dir())
        self.assertFalse((self.workspaces[EXAMPLE_CORE_REPO] / ".tmp").exists())
        self.assertIn("Workspace-write permits project tests", call["prompt"])

    def test_workspace_write_requires_host_attempt_tmpdir(self):
        result, _ = self.invoke(
            self.context("work"),
            {"summary": "verified tests", "changes": []},
            kind="work",
            sandbox="workspace-write",
            provide_attempt_tmpdir=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires TODO Flow to provide an attempt TMPDIR", result.stderr)
        self.assertFalse((self.workspaces[EXAMPLE_CORE_REPO] / ".tmp").exists())

    def test_malformed_context_and_proposal_counterexamples_fail_closed(self):
        valid_work = {
            "summary": "change",
            "changes": [self.full_change(f"{EXAMPLE_CORE_REPO}:README.md")],
        }
        cases = []

        def bad_context(name, mutate):
            value = self.context()
            mutate(value)
            cases.append((name, value, {"summary": "unused"}, "assess", False))

        def bad_output(name, response, *, kind="assess", raw=False):
            cases.append((name, self.context(kind), response, kind, raw))

        bad_context("protocol bool", lambda value: value.update(worker_protocol=True))
        bad_context("protocol string", lambda value: value.update(worker_protocol="2"))
        bad_context("legacy protocol", lambda value: value.update(worker_protocol=1))
        bad_context("unsupported watch role", lambda value: value["task"].update(kind="watch"))
        bad_context("task type mixing", lambda value: value.update(task=[]))
        bad_context("path map type mixing", lambda value: value.update(paths=[]))
        bad_context("short git object id", lambda value: value.update(head="1234"))
        bad_context(
            "missing workspace", lambda value: value.update(workspace=str(self.root / "missing"))
        )
        bad_context("repository list instead of map", lambda value: value.update(repositories=[]))
        bad_context(
            "duplicate repository workspace",
            lambda value: value["repositories"][EXAMPLE_REPORTING_REPO].update(
                workspace=value["repositories"][EXAMPLE_CORE_REPO]["workspace"]
            ),
        )
        bad_context("missing repository head", lambda value: value.update(repository_heads={}))
        bad_context(
            "stale aggregate generation", lambda value: value.update(aggregate_head="0" * 64)
        )
        bad_context("primary head disagreement", lambda value: value.update(head="f" * 40))
        bad_context(
            "missing repository root",
            lambda value: value["repositories"][EXAMPLE_CORE_REPO].update(path=""),
        )
        cases.append(("malformed input JSON", "{", {"summary": "unused"}, "assess", False))
        cases.append(
            (
                "duplicate input JSON property",
                '{"worker_protocol":2,"worker_protocol":2}',
                {"summary": "unused"},
                "assess",
                False,
            )
        )
        bad_output("malformed output JSON", "{", raw=True)
        bad_output("duplicate output JSON property", '{"summary":"a","summary":"b"}', raw=True)
        bad_output("top-level array", [])
        bad_output("summary number", {"summary": 7})
        bad_output("unknown result field", {"summary": "ok", "extra": True})
        bad_output("changes type mixing", {"summary": "ok", "changes": "README.md"})
        bad_output("assess proposes changes", valid_work)
        bad_output("wrong role disposition", {"summary": "ok", "triage": []})
        bad_output(
            "invalid nested review condition",
            {"summary": "review", "verdict": "met", "conditions": [{"id": "c1"}]},
            kind="review",
        )
        bad_output(
            "replace format bad head",
            {
                "summary": "bad replacement",
                "changes": [
                    {
                        "path": f"{EXAMPLE_CORE_REPO}:README.md",
                        "format": "replace-v1",
                        "base_head": "short",
                        "sha256": "0" * 64,
                        "edits": [{"old": "before", "new": "after"}],
                    }
                ],
            },
            kind="work",
        )
        invalid_paths = (
            ("repository name case", f"{EXAMPLE_CORE_REPO.upper()}:README.md"),
            ("unknown repository name", "ghost:README.md"),
            ("missing repository separator", "README.md"),
            ("parent traversal", f"{EXAMPLE_CORE_REPO}:../README.md"),
            ("absolute path", f"{EXAMPLE_CORE_REPO}:/tmp/README.md"),
            ("backslash path", f"{EXAMPLE_CORE_REPO}:docs\\README.md"),
            ("decomposed unicode path", f"{EXAMPLE_CORE_REPO}:e\u0301.md"),
            ("NUL path", f"{EXAMPLE_CORE_REPO}:bad\u0000.md"),
        )
        for name, path in invalid_paths:
            bad_output(
                name, {"summary": "bad path", "changes": [self.full_change(path)]}, kind="work"
            )
        bad_output(
            "case-folded path alias",
            {
                "summary": "aliases",
                "changes": [
                    self.full_change(f"{EXAMPLE_CORE_REPO}:Readme.md"),
                    self.full_change(f"{EXAMPLE_CORE_REPO}:README.md"),
                ],
            },
            kind="work",
        )
        bad_output(
            "nested ancestor and descendant paths",
            {
                "summary": "nested",
                "changes": [
                    self.full_change(f"{EXAMPLE_CORE_REPO}:docs.md"),
                    self.full_change(f"{EXAMPLE_CORE_REPO}:docs.md/child.md"),
                ],
            },
            kind="work",
        )
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.workspaces[EXAMPLE_CORE_REPO] / "escape.md").symlink_to(outside)
        bad_output(
            "symlink escape",
            {"summary": "symlink", "changes": [self.full_change(f"{EXAMPLE_CORE_REPO}:escape.md")]},
            kind="work",
        )

        self.assertGreaterEqual(len(cases), 30)
        for name, context, response, kind, raw in cases:
            with self.subTest(counterexample=name):
                result, _ = self.invoke(context, response, kind=kind, raw_response=raw)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertIn("codex_exec_adapter:", result.stderr, name)

    def test_timeout_kills_codex_process_and_returns_nonzero(self):
        response = {"summary": "slow"}
        env_capture = self.root / "timeout-capture.json"
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CODEX_CAPTURE"] = str(env_capture)
        env["FAKE_CODEX_RESPONSE"] = json.dumps(response)
        env["FAKE_CODEX_SLEEP"] = "0.5"
        result = subprocess.run(
            [
                sys.executable,
                str(self.adapter),
                "--model",
                "gpt-6-luna",
                "--effort",
                "max",
                "--timeout",
                "0.05",
                "--tmp-dir",
                str(self.root),
            ],
            input=json.dumps(self.context()),
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)
        self.assertFalse(any(self.root.glob("todo-flow-codex-*/schema.json")))

    def test_concurrent_runs_keep_prompts_and_proposals_separate(self):
        def run(summary):
            context = self.context()
            context["task"]["id"] = summary
            result, capture = self.invoke(context, {"summary": summary})
            return result, json.loads(capture.read_text(encoding="utf-8"))

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(run, ("first-generation", "second-generation")))
        self.assertEqual(json.loads(first[0].stdout)["summary"], "first-generation")
        self.assertEqual(json.loads(second[0].stdout)["summary"], "second-generation")
        self.assertNotEqual(first[1]["prompt"], second[1]["prompt"])

    def test_new_repository_generation_reaches_codex_and_stale_binding_is_rejected(self):
        updated = self.context("work")
        updated["repositories"][EXAMPLE_CORE_REPO]["head"] = "c" * 40
        updated["repositories"][EXAMPLE_REPORTING_REPO]["head"] = "d" * 40
        updated["repository_heads"] = {
            name: row["head"] for name, row in updated["repositories"].items()
        }
        updated["head"] = updated["repository_heads"][EXAMPLE_CORE_REPO]
        updated["aggregate_head"] = repositories.fingerprint(updated["repository_heads"])
        result, capture = self.invoke(updated, {"summary": "current generation"}, kind="work")
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = json.loads(capture.read_text(encoding="utf-8"))["prompt"]
        self.assertIn(updated["aggregate_head"], prompt)
        self.assertIn("c" * 40, prompt)

        stale = json.loads(json.dumps(updated))
        stale["aggregate_head"] = "0" * 64
        rejected, _ = self.invoke(stale, {"summary": "must not launch"}, kind="work")
        self.assertNotEqual(rejected.returncode, 0)


class RepositoryConfigTests(unittest.TestCase):
    def valid_specs(self):
        return [
            {
                "name": "core",
                "path": "/workspace/core",
                "base": "main",
                "writable": ["src/*.py"],
                "verify": ["python", "-m", "unittest"],
            },
            {
                "name": "service",
                "path": "/workspace/service",
                "base": "release/next",
                "writable": ["*.py"],
                "verify": ["ruff", "check", "src"],
            },
        ]

    def config(self, repos):
        return {"schema_version": 1, "worker_protocol": 2, "repos": repos}

    def test_optional_repo_list_keeps_legacy_config_valid(self):
        check_config({"schema_version": 1, "worker_protocol": 2})

    def test_multi_repo_config_accepts_exact_named_contract(self):
        check_config(self.config(self.valid_specs()))

    def test_legacy_fingerprint_omits_absent_repo_state_and_multi_state_is_bound(self):
        self.assertEqual(repositories.identity_components({"repo_state": None}), [])
        state = {"web": {"head": "a" * 40}}
        encoded = encode(state)
        self.assertEqual(repositories.identity_components({"repo_state": encoded}), [encoded])

    def test_repo_contract_rejects_24_malformed_or_ambiguous_cases(self):
        valid = self.valid_specs()
        cases = []
        for repos in (None, "repos", [], [valid[0]], [valid[0], {**valid[1], "extra": True}]):
            cases.append(("list-shape", repos))
        for name in ("", ".", "..", "a/b", "a:b", "white space", "contains\x00nul"):
            cases.append(("invalid-name", [{**valid[0], "name": name}, valid[1]]))
        cases.extend(
            [
                ("duplicate-name", [valid[0], {**valid[1], "name": "core"}]),
                ("case-aliased-name", [valid[0], {**valid[1], "name": "CORE"}]),
                ("relative-path", [valid[0], {**valid[1], "path": "../service"}]),
                ("empty-path", [valid[0], {**valid[1], "path": ""}]),
                ("nul-path", [valid[0], {**valid[1], "path": "/repo\x00alias"}]),
                ("duplicate-path", [valid[0], {**valid[1], "path": valid[0]["path"]}]),
                ("non-string-base", [valid[0], {**valid[1], "base": 2}]),
                ("empty-base", [valid[0], {**valid[1], "base": ""}]),
                ("option-base", [valid[0], {**valid[1], "base": "--upload-pack=bad"}]),
                ("traversal-base", [valid[0], {**valid[1], "base": "../main"}]),
                ("empty-writable", [valid[0], {**valid[1], "writable": []}]),
                ("non-string-writable", [valid[0], {**valid[1], "writable": [1]}]),
                ("nul-writable", [valid[0], {**valid[1], "writable": ["*.py\x00"]}]),
                ("empty-verify", [valid[0], {**valid[1], "verify": []}]),
                ("non-string-verify", [valid[0], {**valid[1], "verify": ["ruff", 1]}]),
                ("empty-verify-executable", [valid[0], {**valid[1], "verify": ["", "check"]}]),
                ("nul-verify-arg", [valid[0], {**valid[1], "verify": ["ruff", "x\x00"]}]),
            ]
        )
        self.assertGreaterEqual(len(cases), 24)
        for name, repos in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    check_config(self.config(repos))
        with self.subTest(name="native-worker-cannot-route-repositories"):
            with self.assertRaises(ValueError):
                check_config({**self.config(valid), "worker": {"type": "claude"}})


if __name__ == "__main__":
    unittest.main()
