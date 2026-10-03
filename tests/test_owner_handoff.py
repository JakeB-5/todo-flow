import concurrent.futures
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.adapters import file_lock
from todo_flow.engine import Engine
from todo_flow.store import Conflict


class OwnerHandoffTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def test_followup_waits_for_process_attempt_release(self):
        self.s.start("addition")
        first = self.s.claim("first-driver")
        engine = Engine(self.s)
        original = engine.process_attempt
        finished = threading.Event()
        release = threading.Event()

        @contextmanager
        def delayed_exit(task):
            with original(task):
                yield
                if task["id"] == first["id"]:
                    finished.set()
                    if not release.wait(30):
                        raise RuntimeError("Test did not release the completed attempt")

        result = {
            "summary": "Schedule successor",
            "next": [{"kind": "assess", "purpose": "Continue after ownership release"}],
        }
        with (
            patch.object(engine, "process_attempt", delayed_exit),
            patch("todo_flow.engine.run_worker", return_value=result) as worker,
            concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(engine.execute, first)
            try:
                self.assertTrue(finished.wait(30), "Completion did not reach the exit gate")
                before = self.s.snapshot()
                self.assertEqual(
                    next(t for t in before["tasks"] if t["id"] == first["id"])["status"],
                    "done",
                )
                self.assertTrue(any(t["status"] == "queued" for t in before["tasks"]))
                self.assertIsNone(self.s.claim("second-driver"))
                with self.s.transaction() as connection:
                    self.assertIsNone(self.s.claim("recovery-driver", connection=connection))
                after = self.s.snapshot()
                for key in ("tasks", "attempts", "budgets", "events"):
                    self.assertEqual(after[key], before[key])
                worker.assert_called_once()
                self.s.register({**test_flow.DOC, "id": "second"})
                self.s.start("second")
                other = self.s.claim("other-driver")
                self.assertIsNotNone(other)
                self.assertEqual(other["track"], "second")
            finally:
                release.set()
                future.result(timeout=30)
            successor = self.s.claim("second-driver")
            self.assertIsNotNone(successor)
            self.assertEqual(successor["track"], "addition")
            worker.return_value = {"summary": "Successor executed"}
            engine.execute(successor)
            self.assertEqual(worker.call_count, 2)
        snapshot = self.s.snapshot()
        self.assertFalse(snapshot["decisions"])
        self.assertEqual(
            next(t for t in snapshot["tasks"] if t["id"] == successor["id"])["status"],
            "done",
        )

    def test_external_owner_still_blocks_claim_and_execute(self):
        self.s.start("addition")
        lock = self.s.path / "locks/addition.lock"
        before = self.s.snapshot()
        with file_lock(lock):
            self.assertIsNone(self.s.claim("competing-driver"))
        after = self.s.snapshot()
        for key in ("tasks", "attempts", "budgets", "events"):
            self.assertEqual(after[key], before[key])
        task = self.s.claim("driver")
        self.assertIsNotNone(task)
        engine = Engine(self.s)
        with (
            file_lock(lock),
            patch.object(engine, "ensure_workspace") as workspace,
            patch.object(engine, "fail") as failed,
        ):
            engine.execute(task)
        workspace.assert_not_called()
        failed.assert_called_once()
        self.assertIsInstance(failed.call_args.args[1], Conflict)
        self.assertIn("owned by another runtime", str(failed.call_args.args[1]))
