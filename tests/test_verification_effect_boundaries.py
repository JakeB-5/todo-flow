import json
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import test_flow
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.store import Conflict, encode


class VerificationEffectBoundaryTests(unittest.TestCase):
    @contextmanager
    def candidate(self):
        fixture = test_flow.IntegrationTests()
        fixture.setUp()
        try:
            with patch.dict(os.environ, {"VERIFY_BOUNDARY_TEST": "original"}):
                store = fixture.s
                store.start("addition")
                author = store.claim("author")
                engine = Engine(store)
                workspace = engine.ensure_workspace(author)
                runner = fixture.root / "runner.py"
                runner.write_text("raise SystemExit(0)\n")
                engine.config.update(
                    verify=[sys.executable, str(runner)],
                    verify_identity={
                        "version": 1,
                        "files": [str(runner)],
                        "environment": ["VERIFY_BOUNDARY_TEST"],
                        "nonce": "original",
                    },
                )
                self.assertTrue(engine.verify(author, workspace)["ok"])
                store.finish(
                    author,
                    {
                        "summary": "Ready for independent review",
                        "next": [{"kind": "review", "purpose": "Review candidate"}],
                    },
                )
                reviewer = store.claim("reviewer")
                self.assertNotEqual(author["attempt"], reviewer["attempt"])
                engine.record_review(
                    reviewer,
                    {
                        "summary": "Boundary test review fixture",
                        "verdict": "met",
                        "conditions": [
                            {"id": "sum", "verdict": "met", "evidence": "Test fixture"}
                        ],
                    },
                )
                yield engine, reviewer, workspace, runner
        finally:
            fixture.tearDown()

    @contextmanager
    def effects(self, engine):
        push = Mock(return_value="")
        remote = Mock()
        remote.pr.return_value = {"number": 123}
        engine.remote = remote

        def guarded_command(argv, *args, **kwargs):
            if argv[:2] == ["git", "push"]:
                return push(argv, *args, **kwargs)
            return command(argv, *args, **kwargs)

        with (
            patch("todo_flow.engine.command", side_effect=guarded_command),
            patch(
                "todo_flow.engine.run_verification",
                side_effect=AssertionError("Effect boundaries must not run verification"),
            ),
        ):
            yield push, remote

    def change(self, kind, engine, task, workspace, runner):
        if kind == "runner":
            runner.write_text("raise SystemExit(1)\n")
        elif kind == "environment":
            os.environ["VERIFY_BOUNDARY_TEST"] = "changed"
        elif kind == "nonce":
            engine.config["verify_identity"]["nonce"] = "changed"
        elif kind == "declaration":
            engine.config["verify_identity"]["files"] = []
        elif kind == "argv":
            engine.config["verify"] = [sys.executable, "-c", "raise SystemExit(0)"]
        elif kind == "missing-input":
            runner.unlink()
        elif kind == "dirty":
            (workspace / "calc.py").write_text("# Concurrent operator edit\n")
        elif kind == "head":
            command(["git", "commit", "--allow-empty", "-m", "Concurrent head"], workspace)
        elif kind == "missing-evidence":
            engine.update(task, verification=None)
        else:
            record = json.loads(engine.store.track(task["track"])["verification"])
            if kind == "legacy":
                del record["identity"]
            elif kind == "unsupported":
                record["identity"]["version"] = 999
            elif kind == "tree":
                record["tree"] = "0" * 40
            elif kind == "failed":
                record["ok"] = False
            else:
                raise AssertionError("Unknown mutation: " + kind)
            engine.update(task, verification=encode(record))

    def test_current_evidence_allows_gate_and_publication_without_reverification(self):
        with self.candidate() as (engine, task, workspace, runner):
            before = engine.store.track(task["track"])
            with self.effects(engine) as (push, remote):
                self.assertEqual(engine.gate(task)["head"], before["head"])
                engine.publish(task, workspace, test_flow.DOC)
            push.assert_called_once_with(
                ["git", "push", "origin", before["branch"]], workspace
            )
            remote.pr.assert_called_once()
            after = engine.store.track(task["track"])
            self.assertEqual(after["verification"], before["verification"])
            self.assertEqual(after["review"], before["review"])
            self.assertEqual(after["pr"], 123)

    def test_changed_or_invalid_evidence_blocks_both_boundaries(self):
        for kind in (
            "runner",
            "environment",
            "nonce",
            "declaration",
            "argv",
            "missing-input",
            "dirty",
            "head",
            "missing-evidence",
            "legacy",
            "unsupported",
            "tree",
            "failed",
        ):
            with self.subTest(kind=kind), self.candidate() as candidate:
                engine, task, workspace, runner = candidate
                self.change(kind, *candidate)
                with self.effects(engine) as (push, remote):
                    with self.assertRaises(Conflict):
                        engine.gate(task)
                    with self.assertRaises(Conflict):
                        engine.publish(task, workspace, test_flow.DOC)
                push.assert_not_called()
                remote.pr.assert_not_called()

    def test_changes_while_waiting_for_publish_lock_block_effects(self):
        for kind in (
            "runner",
            "environment",
            "nonce",
            "legacy",
            "unsupported",
            "missing-evidence",
            "dirty",
            "head",
        ):
            with self.subTest(kind=kind), self.candidate() as candidate:
                engine, task, workspace, runner = candidate
                acquired = []

                @contextmanager
                def changed_lock(path, blocking=False):
                    self.assertEqual(path, engine.store.path / "locks" / "publish.lock")
                    self.assertTrue(blocking)
                    acquired.append(path)
                    self.change(kind, *candidate)
                    yield

                with (
                    self.effects(engine) as (push, remote),
                    patch("todo_flow.engine.file_lock", side_effect=changed_lock),
                ):
                    with self.assertRaises(Conflict):
                        engine.publish(task, workspace, test_flow.DOC)
                self.assertEqual(len(acquired), 1)
                push.assert_not_called()
                remote.pr.assert_not_called()

    def test_valid_verification_does_not_replace_independent_review(self):
        with self.candidate() as (engine, task, workspace, runner):
            engine.update(task, review=None)
            with self.effects(engine) as (push, remote):
                with self.assertRaisesRegex(Conflict, "independent review"):
                    engine.gate(task)
            push.assert_not_called()
            remote.pr.assert_not_called()

    def test_lost_ownership_while_waiting_for_publish_lock_blocks_effects(self):
        with self.candidate() as (engine, task, workspace, runner):

            @contextmanager
            def cancelled_lock(path, blocking=False):
                engine.store.control(task["track"], "cancel")
                yield

            with (
                self.effects(engine) as (push, remote),
                patch("todo_flow.engine.file_lock", side_effect=cancelled_lock),
            ):
                with self.assertRaises(Conflict):
                    engine.publish(task, workspace, test_flow.DOC)
            push.assert_not_called()
            remote.pr.assert_not_called()
