import subprocess
import unittest
from unittest.mock import patch

from todo_flow.adapters import command


FETCH = ["git", "fetch", "origin", "main"]
RACE = (
    "From /local/remote\n"
    "error: fetching ref refs/remotes/origin/main failed: incorrect old value provided\n"
)


def result(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(FETCH, code, stdout, stderr)


class FetchRetryTests(unittest.TestCase):
    def test_ref_race_recovers_and_preserves_command_options(self):
        with patch(
            "todo_flow.adapters.subprocess.run",
            side_effect=[result(1, stderr=RACE), result(0, "updated\n", "details\n")],
        ) as run:
            value = command(FETCH, cwd="/checkout", timeout=30, include_stderr=True)
        self.assertEqual(value, "updated\ndetails")
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args, (FETCH,))
            self.assertEqual(call.kwargs["cwd"], "/checkout")
            self.assertEqual(call.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertLessEqual(call.kwargs["timeout"], 30)

    def test_persistent_ref_race_is_bounded_and_reported(self):
        with patch("todo_flow.adapters.subprocess.run", return_value=result(1, stderr=RACE)) as run:
            with self.assertRaisesRegex(RuntimeError, "incorrect old value provided"):
                command(FETCH)
        self.assertEqual(run.call_count, 3)

    def test_other_failures_and_mutating_commands_are_not_retried(self):
        cases = [
            (FETCH, "fatal: Authentication failed\n"),
            (FETCH, "fatal: couldn't find remote ref main\n"),
            (FETCH, "fatal: Unable to create ref lock: File exists\n"),
            (["git", "push", "origin", "main"], RACE),
            (["gh", "api", "repos/example/project/issues"], RACE),
        ]
        for argv, stderr in cases:
            with self.subTest(argv=argv, stderr=stderr):
                with patch(
                    "todo_flow.adapters.subprocess.run",
                    return_value=result(1, stderr=stderr),
                ) as run:
                    with (
                        self.assertRaisesRegex(RuntimeError, "incorrect old value provided")
                        if stderr == RACE
                        else self.assertRaises(RuntimeError)
                    ):
                        command(argv)
                self.assertEqual(run.call_count, 1)

    def test_retry_uses_remaining_timeout_budget(self):
        with (
            patch("todo_flow.adapters.time.monotonic", side_effect=[100, 104, 105]),
            patch(
                "todo_flow.adapters.subprocess.run",
                side_effect=[result(1, stderr=RACE), result(0, "ok")],
            ) as run,
        ):
            self.assertEqual(command(FETCH, timeout=10), "ok")
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 10)
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 5)

    def test_exhausted_budget_does_not_start_another_fetch(self):
        with (
            patch("todo_flow.adapters.time.monotonic", side_effect=[100, 111]),
            patch("todo_flow.adapters.subprocess.run", return_value=result(1, stderr=RACE)) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "incorrect old value provided"):
                command(FETCH, timeout=10)
        self.assertEqual(run.call_count, 1)

    def test_timeout_is_not_retried(self):
        with patch(
            "todo_flow.adapters.subprocess.run",
            side_effect=subprocess.TimeoutExpired(FETCH, 10),
        ) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                command(FETCH, timeout=10)
        self.assertEqual(run.call_count, 1)
