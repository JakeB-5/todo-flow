"""Acceptance tests for tracks spanning multiple Git repositories."""

import json
import sys
import unittest
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
from todo_flow import clean_integration
from todo_flow import proposal_application
from todo_flow import condition_evidence
from todo_flow.delivery_display import delivery_summary


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

    def configure_repositories(self, *, second_verify=None, endpoint="review", github=None):
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
            [str(self.repo.resolve())] * 2 + [str(self.second_repo.resolve())] * 2,
        )
        self.assertEqual(
            [argv[3] for argv, _ in calls if argv[:3] == ["gh", "repo", "view"]],
            ["example/core", "example/service"],
        )

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
                self.s.path, self.s.track("addition"), review["conditions"][0]
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
