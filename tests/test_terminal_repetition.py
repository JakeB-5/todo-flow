"""Repeat real worker/adapter boundaries with explicitly synthetic CLI responses.

Visible backends model bridge completion; they do not launch Orca or tmux.
Headless runs a local Python command through the real process supervisor.
These are candidate measurements, not measurements of an older revision.
"""

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from todo_flow.launchers import accept_terminal
from todo_flow.process_inventory import ProcessInventory
from todo_flow.process_launch import LaunchGate
from todo_flow.terminal_slots import TerminalCapacityError, TerminalSlots
from todo_flow.worker import run_worker


class TerminalRepetitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.slots = TerminalSlots(self.root, concurrency=2, idle_limit=1)
        self.rows = {}
        self.created = 0
        self.closed = 0
        self.max_active = 0
        self.max_physical = 0
        self.preserved = {}
        self.executions = set()

    def accept(self, reservation, record, folder):
        accepted = accept_terminal(reservation, record, folder)
        counts = self.slots.counts(self.slots.snapshot())
        self.max_active = max(self.max_active, counts["active"])
        self.assertLessEqual(counts["active"] + counts["reserved"], 2)
        return accepted

    def finish_bridge(self):
        spec = json.loads((self.folder / "terminal-spec.json").read_text())
        identity = spec["launch_identity"]
        gate = LaunchGate(**identity)
        with gate.launching():
            pass
        event = gate._advance(gate._event(), "cleaning", "Synthetic exit", {"fixture": True})
        gate._advance(
            event,
            "confirmed",
            "Synthetic group exit",
            {
                "identity": {k: identity[k] for k in ("track", "attempt", "execution")},
                "outcome": "group-exited",
                "proof": "Synthetic bridge fixture; no OS process was launched",
            },
        )
        self.finished = time.time()
        (self.folder / "terminal-process.json").write_text(
            json.dumps(
                {
                    "status": "exited",
                    "returncode": 0,
                    "cleanup_confirmed": True,
                    "finished_at": self.finished,
                }
            )
        )
        (self.folder / "output.json").write_text(json.dumps({"summary": self.folder.name}))
        (self.folder / "stderr.log").write_text("diagnostic " + self.folder.name)
        for name in ("output.json", "stderr.log", "terminal-spec.json", "terminal-process.json"):
            path = self.folder / name
            self.preserved[path] = path.read_bytes()

    def create(self):
        self.created += 1
        handle = f"term-{self.created}" if self.backend == "orca" else f"@{self.created}"
        self.rows[handle] = {
            "handle": handle,
            "ptyId": f"pty-{self.created}",
            "incarnationId": f"incarnation-{self.created}",
            "tabId": f"tab-{self.created}",
            "executionHostId": "local",
            "worktreeId": "fixture",
            "title": "TODO " + self.folder.name,
            "connected": False,
            "writable": False,
            "orphaned": False,
            "lastOutputAt": time.time() * 1000,
            "preview": "TODO Flow worker exited: 0",
        }
        self.max_physical = max(self.max_physical, len(self.rows))
        self.finish_bridge()
        return handle

    def cli(self, argv, **kwargs):
        if self.backend != "orca":
            if "list-windows" in argv:
                # A retained user window makes even the empty owned inventory complete.
                output = "\n".join(["@0", *self.rows]) + "\n"
            else:
                handle = self.create()
                output = handle + "\n"
                if self.backend == "tmux":
                    del self.rows[handle]  # Model automatic window removal on bridge exit.
            return subprocess.CompletedProcess(argv, 0, output, "")
        action = argv[2]
        if action == "create":
            self.assertTrue(argv[argv.index("--command") + 1].startswith("exec "))
            handle = self.create()
            self.rows[handle]["title"] = argv[argv.index("--title") + 1]
            result = {"terminal": dict(self.rows[handle])}
        elif action == "list":
            # User and dashboard tabs are present but never eligible for retirement.
            foreign = [
                {
                    "handle": name,
                    "ptyId": name,
                    "tabId": name,
                    "executionHostId": "local",
                    "worktreeId": "fixture",
                }
                for name in ("user", "dashboard")
            ]
            rows = [*foreign, *self.rows.values()]
            result = {
                "terminals": rows,
                "totalCount": len(rows),
                "truncated": False,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            }
        else:
            handle = argv[argv.index("--terminal") + 1]
            self.assertIn(handle, self.rows)
            if action == "wait":
                result = {
                    "wait": {
                        "handle": handle,
                        "condition": "exit",
                        "satisfied": True,
                        "status": "exited",
                        "exitCode": 0,
                    }
                }
            elif action == "show":
                result = {"terminal": dict(self.rows[handle])}
            elif action == "close":
                events = json.loads((self.folder / "orca-retirement.json").read_text())
                self.assertTrue(events[-1]["close_intent"])
                self.closed += 1
                del self.rows[handle]
                result = {"close": {"handle": handle, "ptyKilled": False}}
            else:
                self.fail(f"Unexpected CLI operation: {argv}")
        payload = {"ok": True, "result": result, "_meta": {"runtimeId": "fixture-runtime"}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    def remember(self, task, visible):
        inventory = ProcessInventory(self.root, task["track"], task["attempt"]).read()
        (execution,) = inventory["executions"]
        self.assertNotIn(execution, self.executions)
        self.executions.add(execution)
        gate = LaunchGate(self.root, task["track"], task["attempt"], execution)
        events = gate.barrier.history()
        self.assertEqual(events[0]["state"], "intent")
        self.assertIn("running", [event["state"] for event in events])
        self.assertEqual(events[-1]["state"], "confirmed")
        names = ["input.json", "output.json", "stderr.log", "launch.json"]
        if visible:
            names += ["terminal-spec.json", "terminal-process.json"]
        paths = [self.folder / name for name in names] + [gate.barrier.path, gate.path]
        for path in paths:
            if path in self.preserved:
                self.assertEqual(path.read_bytes(), self.preserved[path])
            self.preserved[path] = path.read_bytes()

    def repeat(self, backend):
        self.backend = backend
        launcher = {"backend": backend}
        if backend == "orca":
            launcher.update(cli="synthetic-orca", worktree="id:fixture", repo=str(self.root))
        elif backend == "tmux":
            server = socket.socket(socket.AF_UNIX)
            self.addCleanup(server.close)
            server.bind(str(self.root / "tmux.sock"))
            launcher["socket"] = str(self.root / "tmux.sock")
        else:
            launcher["argv"] = ["synthetic-terminal", "{command}"]
        blocked = 0
        for number in range(50):
            task = {
                "id": f"task-{number}",
                "track": f"track-{number}",
                "attempt": f"attempt-{number}",
                "generation": 1,
                "kind": "work" if number % 2 == 0 else "review",
            }
            self.folder = self.root / "attempts" / task["attempt"]
            config = {
                "worker": {"type": "command", "argv": [sys.executable, "-c", "pass"]},
                "worker_protocol": 2,
            }
            with (
                patch("todo_flow.worker.select_launcher", return_value=launcher),
                patch("todo_flow.launchers.subprocess.run", side_effect=self.cli),
                patch("todo_flow.launchers.accept_terminal", side_effect=self.accept),
            ):
                if backend == "terminal" and number >= 2:
                    with self.assertRaises(TerminalCapacityError):
                        run_worker(
                            config, {"workspace": str(self.root)}, task, self.root, lambda _: None
                        )
                    blocked += 1
                    self.assertFalse((self.folder / "terminal-spec.json").exists())
                    continue
                result = run_worker(
                    config, {"workspace": str(self.root)}, task, self.root, lambda _: None
                )
            self.assertEqual(result["summary"], task["attempt"])
            report = json.loads((self.folder / "terminal-retirement.json").read_text())
            self.assertEqual(report["status"], "preserved" if backend == "terminal" else "closed")
            self.remember(task, visible=True)
        expected = 2 if backend == "terminal" else 50
        self.assertEqual(self.created, expected)
        self.assertEqual(len(self.executions), expected)
        self.assertEqual(blocked, 48 if backend == "terminal" else 0)
        self.assertEqual(self.closed, 50 if backend == "orca" else 0)
        self.assertEqual(len(self.rows), 2 if backend == "terminal" else 0)
        counts = self.slots.counts(self.slots.snapshot())
        self.assertEqual(counts["closed"], 0 if backend == "terminal" else 50)
        self.assertEqual(counts["quarantined"], 2 if backend == "terminal" else 0)
        maximum = 2 if backend == "terminal" else 1
        self.assertEqual(self.max_physical, maximum)
        self.assertEqual(self.slots.snapshot()["max_owned"], maximum)
        self.assertEqual(self.max_active, 1)
        for path, content in self.preserved.items():
            self.assertEqual(path.read_bytes(), content)
        print(
            json.dumps(
                {
                    "fixture": "synthetic-cli-candidate",
                    "backend": backend,
                    "requests": 50,
                    "created": self.created,
                    "reused": 0,
                    "reclaimed": counts["closed"],
                    "close_calls": self.closed,
                    "preserved": counts["quarantined"],
                    "blocked": blocked,
                    "max_owned": maximum,
                    "max_active": self.max_active,
                    "evidence_executions": len(self.executions),
                },
                sort_keys=True,
            )
        )

    def test_orca_fifty_sequential_workers_retire_before_return(self):
        self.repeat("orca")

    def test_tmux_fifty_sequential_workers_observe_automatic_removal(self):
        self.repeat("tmux")

    def test_custom_fifty_requests_preserve_two_and_block_forty_eight(self):
        self.repeat("terminal")

    def test_headless_fifty_local_workers_preserve_evidence_without_tabs(self):
        program = (
            "import json,pathlib,sys; data=json.load(sys.stdin); "
            "number=json.loads(pathlib.Path(data['paths']['sequence']).read_text()); "
            "print(json.dumps({'summary': str(number)})); "
            "print('diagnostic ' + str(number), file=sys.stderr)"
        )
        config = {
            "worker": {"type": "command", "argv": [sys.executable, "-c", program]},
            "worker_protocol": 2,
            "worker_launcher": "headless",
        }
        with patch("todo_flow.worker.spawn_terminal") as visible:
            for number in range(50):
                task = {
                    "id": f"task-{number}",
                    "track": f"track-{number}",
                    "attempt": f"attempt-{number}",
                    "generation": 1,
                    "kind": "work",
                }
                self.folder = self.root / "attempts" / task["attempt"]
                result = run_worker(
                    config,
                    {"workspace": str(self.root), "sequence": number},
                    task,
                    self.root,
                    lambda _: None,
                )
                self.assertEqual(result["summary"], str(number))
                self.assertEqual((self.folder / "stderr.log").read_text(), f"diagnostic {number}\n")
                self.remember(task, visible=False)
            visible.assert_not_called()
        self.assertFalse(self.slots.path.exists())
        self.assertEqual(len(self.executions), 50)
        for path, content in self.preserved.items():
            self.assertEqual(path.read_bytes(), content)
        print(
            json.dumps(
                {
                    "fixture": "local-python-candidate",
                    "backend": "headless",
                    "requests": 50,
                    "created": 0,
                    "reused": 0,
                    "reclaimed": 0,
                    "preserved": 0,
                    "blocked": 0,
                    "max_owned": 0,
                    "max_active": 0,
                    "evidence_executions": 50,
                },
                sort_keys=True,
            )
        )
