"""Synthetic HTTP coverage; no Orca or model process is launched."""

import json
import unittest

import test_web


class TaskLaunchHttpTests(unittest.TestCase):
    setUp = test_web.HttpTests.setUp
    tearDown = test_web.HttpTests.tearDown
    get = test_web.HttpTests.get

    def create_task(self):
        with self.s.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(id,track,kind,purpose,created,updated) VALUES(?,?,?,?,?,?)",
                ("fixture-task", "example", "work", "Synthetic launch display", 1, 1),
            )
        return "fixture-task"

    def test_latest_attempt_and_receipt_states(self):
        task_id = self.create_task()
        with self.s.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,?)",
                ("launch-old", task_id, 1, "running"),
            )
        folder = self.s.path / "attempts/launch-old"
        folder.mkdir(parents=True, exist_ok=True)
        receipt = folder / "launch.json"
        receipt.write_text(
            json.dumps(
                {
                    "backend": "orca",
                    "status": "accepted",
                    "argv": ["PRIVATE_ARG"],
                    "handle": {"socket": "PRIVATE_SOCKET"},
                    "worktree": {"internal": "PRIVATE_WORKTREE"},
                    "terminal": {"internal": "PRIVATE_TERMINAL"},
                    "selection": {
                        "requested": "auto",
                        "reason": "native_contract_unverified",
                        "private": "PRIVATE_SELECTION",
                    },
                }
            ),
            encoding="utf-8",
        )
        before = receipt.read_bytes()
        result = self.get("/api/tasks/" + task_id)
        launch = result["launch"]
        self.assertEqual(launch["attempt"], result["attempt"]["id"])
        self.assertEqual(launch["evidence"], "available")
        self.assertEqual(set(launch), {"attempt", "evidence", "summaries"})
        self.assertEqual(set(launch["summaries"]), {"en", "ko"})
        self.assertEqual(launch["summaries"]["en"]["backend"], "Orca terminal (command worker)")
        self.assertIn("worker start not established", launch["summaries"]["en"]["status"])
        self.assertIn("워커 시작 근거 아님", launch["summaries"]["ko"]["status"])
        self.assertIn(
            "do not prove worker start or completion", launch["summaries"]["en"]["process"]
        )
        self.assertIn("미구현", launch["summaries"]["ko"]["native"])
        self.assertIn("미실시", launch["summaries"]["ko"]["validation"])
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertEqual(receipt.read_bytes(), before)

        with self.s.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,?)",
                ("launch-new", task_id, 2, "running"),
            )
        latest = self.s.path / "attempts/launch-new"
        latest.mkdir(parents=True, exist_ok=True)
        for content, evidence in (
            (None, "missing"),
            ("{", "unreadable"),
            ('{"selection": []}', "unreadable"),
            (" " * 65537, "unreadable"),
        ):
            if content is not None:
                (latest / "launch.json").write_text(content, encoding="utf-8")
            result = self.get("/api/tasks/" + task_id)
            self.assertEqual(result["attempt"]["id"], "launch-new")
            self.assertEqual(result["launch"]["attempt"], "launch-new")
            self.assertEqual(result["launch"]["evidence"], evidence)
            self.assertNotIn("Orca terminal", json.dumps(result["launch"]))
            self.assertIn("not implemented", result["launch"]["summaries"]["en"]["native"])
            self.assertEqual(result["launch"]["summaries"]["en"]["status"], "Not recorded")
            self.assertEqual(receipt.read_bytes(), before)
            if content is None:
                self.assertFalse((latest / "launch.json").exists())
            else:
                self.assertEqual((latest / "launch.json").read_text(encoding="utf-8"), content)

        (latest / "launch.json").write_text(
            json.dumps(
                {
                    "backend": None,
                    "status": "unavailable",
                    "selection": {"requested": "orca", "reason": "remote_host_mismatch"},
                }
            ),
            encoding="utf-8",
        )
        failed_before = (latest / "launch.json").read_bytes()
        summary = self.get("/api/tasks/" + task_id)["launch"]["summaries"]
        self.assertEqual((latest / "launch.json").read_bytes(), failed_before)
        self.assertEqual(summary["en"]["status"], "Selection failed before launch")
        self.assertEqual(summary["ko"]["backend"], "선택된 backend 없음")
        self.assertEqual(summary["ko"]["reason"], "Orca host가 로컬이 아님")

    def test_task_without_attempt_has_no_launch_evidence(self):
        result = self.get("/api/tasks/" + self.create_task())
        self.assertIsNone(result["attempt"])
        self.assertIsNone(result["launch"]["attempt"])
        self.assertEqual(result["launch"]["evidence"], "missing")

    def test_equal_start_times_use_descending_id_for_response_and_receipt(self):
        task_id = self.create_task()
        # Insert the smaller ID first so started-only ordering selects the wrong row.
        with self.s.transaction() as connection:
            for attempt in ("launch-a", "launch-z"):
                connection.execute(
                    "INSERT INTO attempts(id,task,started,status) VALUES(?,?,?,?)",
                    (attempt, task_id, 3, "running"),
                )
        receipts = {}
        for attempt, backend in (("launch-a", "orca"), ("launch-z", "headless")):
            folder = self.s.path / "attempts" / attempt
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "launch.json"
            path.write_text(
                json.dumps({"backend": backend, "status": "selected"}),
                encoding="utf-8",
            )
            receipts[path] = path.read_bytes()
        for _ in range(2):
            result = self.get("/api/tasks/" + task_id)
            self.assertEqual(result["attempt"]["id"], "launch-z")
            self.assertEqual(result["launch"]["attempt"], result["attempt"]["id"])
            self.assertEqual(result["launch"]["summaries"]["en"]["backend"], "Headless process")
        for path, before in receipts.items():
            self.assertEqual(path.read_bytes(), before)
