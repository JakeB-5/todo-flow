"""Real PTYs/process groups and synthetic Claude/Orca; no account or network use."""

import json
import os
import pty
import shlex
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_flow
from todo_flow.engine import Engine
from todo_flow.claude_session import adopt
from todo_flow.managed_workspace import receipt_path
from todo_flow.process_barrier import ProcessBarrier
from todo_flow.store import Conflict
from todo_flow.worker import run_worker
from todo_flow.worker_stop import worker_stop_summary
from todo_flow.workspace_creation import WorkspaceCreationGate


CLAUDE = r"""
import json,os,sys,time,uuid
from pathlib import Path
if sys.argv[1:]==['--help']:
 print('--safe-mode --restricted --session-id --tools --permission-mode');sys.exit()
assert '-p' not in sys.argv and '--print' not in sys.argv
assert '--no-session-persistence' not in sys.argv
assert all(os.isatty(fd) for fd in (0,1,2))
assert '--safe-mode' in sys.argv and '--restricted' in sys.argv
assert sys.argv[sys.argv.index('--tools')+1]=='Read,Glob,Grep'
assert sys.argv[sys.argv.index('--permission-mode')+1]=='dontAsk'
session=sys.argv[sys.argv.index('--session-id')+1]
prompt=sys.argv[-1]
root=Path(os.environ['CLAUDE_CONFIG_DIR'])
with (root/'launches').open('a') as f:f.write(session+'\n')
path=root/'projects/fixture'/f'{session}.jsonl'
path.parent.mkdir(parents=True,exist_ok=True)
def row(kind, **extra):
 return dict(type=kind,sessionId=session,cwd=os.getcwd(),uuid=str(uuid.uuid4()),isSidechain=False,**extra)
def emit(obj):
 with path.open('a') as f:f.write(json.dumps(obj)+'\n')
emit(row('user',message={'content':prompt}))
print('Synthetic interactive Claude session',flush=True)
if (root/'hang').exists():
 child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
 import subprocess
 subprocess.Popen([sys.executable,'-c',child])
 time.sleep(60)
if (root/'wrong-session').exists():session='foreign'
emit(row('assistant',message={'model':'synthetic-claude','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'summary':'Read-only interactive result','changes':[{'path':'calc.py','content':'# proposal\n'*20000}]})}]}))
if (root/'partial').exists():time.sleep(60)
emit(row('system',subtype='turn_duration',durationMs=123))
time.sleep(60)
"""


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX terminals")
class ClaudeSessionTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self):
        self.s.start("addition")
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET kind='work' WHERE track='addition'")
        self.task = self.s.claim("claude-fixture")
        self.engine = Engine(self.s)
        self.workspace = self.engine.ensure_workspace(self.task)
        self.context = self.engine.context(self.task, self.workspace)
        self.before = (self.workspace / "calc.py").read_bytes()
        self.fixture = self.repo.parent / "claude-fixture"
        self.fixture.mkdir()
        self.binary = self.fixture / "claude"
        self.binary.write_text("#!" + sys.executable + "\n" + CLAUDE)
        self.binary.chmod(0o700)
        self.home = self.fixture / "account"
        self.home.mkdir()
        self.owned = {
            "id": "fixture-repo::" + str(self.workspace),
            "instanceId": "fixture-worktree",
            "hostId": "local",
            "head": self.context["head"],
            "path": str(self.workspace),
        }
        receipt_path(WorkspaceCreationGate(self.s.path, "addition")).write_text(
            json.dumps({"version": 1, "track": "addition", "observation": self.owned})
        )
        self.launcher = {
            "backend": "orca",
            "cli": "fixture-orca",
            "worktree": "id:fixture-repo::" + str(self.repo),
            "repo": str(self.repo),
            "selection": {"requested": "auto", "backend": "orca"},
        }
        self.config = {**self.engine.config, "worker": {"type": "claude"}, "worker_timeout": 10}
        self.folder = self.s.path / "attempts" / self.task["attempt"]
        self.terminal_output = bytearray()
        self.bridges = []
        self.readers = []
        environment = patch.dict(
            os.environ,
            {
                "PATH": str(self.fixture) + os.pathsep + os.environ["PATH"],
                "CLAUDE_CONFIG_DIR": str(self.home),
                "ORCA_WORKTREE_ID": self.owned["id"],
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.close_bridges)

    def close_bridges(self):
        for bridge in self.bridges:
            try:
                bridge.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bridge.terminate()
                bridge.wait(timeout=10)
        for thread in self.readers:
            thread.join(timeout=5)

    def create_terminal(self, cli, args, cwd):
        self.assertEqual(args[:2], ["terminal", "create"])
        self.assertEqual(args[args.index("--worktree") + 1], "id:" + self.owned["id"])
        argv = shlex.split(args[args.index("--command") + 1])
        self.assertEqual(argv.pop(0), "exec")
        master, slave = pty.openpty()
        bridge = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True
        )
        os.close(slave)
        self.bridges.append(bridge)

        def drain():
            try:
                while chunk := os.read(master, 65536):
                    self.terminal_output.extend(chunk)
            except OSError:
                pass
            finally:
                os.close(master)

        reader = threading.Thread(target=drain, daemon=True)
        self.readers.append(reader)
        reader.start()
        return {"terminal": {"handle": "fixture-terminal", "worktreeId": self.owned["id"]}}

    def execute(self, heartbeat=None):
        with (
            self.engine.process_attempt(self.task),
            patch("todo_flow.worker.select_launcher", return_value=self.launcher),
            patch("todo_flow.claude_session.orca_result", return_value={"worktree": self.owned}),
            patch("todo_flow.claude_session.status_hook", return_value=None),
            patch("todo_flow.launchers.orca_result", side_effect=self.create_terminal),
            patch("todo_flow.worker.retire_launch") as retire,
        ):
            try:
                return run_worker(
                    self.config,
                    self.context,
                    self.task,
                    self.s.path,
                    heartbeat or (lambda pid: self.s.heartbeat(self.task, pid)),
                )
            finally:
                if self.bridges:
                    retire.assert_called_once_with(self.folder)

    def test_interactive_roundtrip_uses_owned_worktree_and_stops_after_full_proposal(self):
        self.prepare()
        result = self.execute()
        self.assertEqual(result["summary"], "Read-only interactive result")
        self.assertEqual(result["changes"][0]["content"], "# proposal\n" * 20000)
        self.assertEqual((self.workspace / "calc.py").read_bytes(), self.before)
        self.assertIn(b"Synthetic interactive Claude session", self.terminal_output)
        launch = json.loads((self.folder / "launch.json").read_text())
        self.assertEqual(launch["execution_mode"], "orca-interactive")
        self.assertEqual(launch["worktree"], "id:" + self.owned["id"])
        self.assertEqual(launch["status"], "completed")
        record = json.loads((self.folder / "claude-session.json").read_text())
        self.assertEqual(record["task"], self.task)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["model"], "synthetic-claude")
        ProcessBarrier(self.s.path, "addition").require_clear()
        summary = worker_stop_summary(self.s.path, "addition", self.task["attempt"])
        execution = next(row for row in summary["executions"] if row["proposal"] is not None)
        self.assertTrue(execution["process"]["group_exit_confirmed"])
        self.assertTrue(execution["proposal"]["validated"])

    def test_cancel_stops_the_owned_group_and_never_replays_as_headless(self):
        self.prepare()
        (self.home / "hang").touch()

        def cancel(pid):
            if (self.folder / "claude-session.json").exists():
                record = json.loads((self.folder / "claude-session.json").read_text())
                if record.get("turn"):
                    self.s.control("addition", "cancel")
                    raise Conflict("Synthetic user cancellation")
            self.s.heartbeat(self.task, pid)

        with self.assertRaisesRegex(Conflict, "cancellation"):
            self.execute(cancel)
        receipt = json.loads((self.folder / "terminal-process.json").read_text())
        self.assertEqual(receipt["returncode"], 130)
        self.assertTrue(receipt["cleanup_confirmed"])
        self.assertEqual(len((self.home / "launches").read_text().splitlines()), 1)
        ProcessBarrier(self.s.path, "addition").require_clear()
        with self.assertRaisesRegex(FileExistsError, "reconcile"):
            run_worker(self.config, self.context, self.task, self.s.path, lambda _: None)

    def test_session_mismatch_fails_after_launch_without_fallback(self):
        self.prepare()
        (self.home / "wrong-session").touch()
        with self.assertRaisesRegex(RuntimeError, "session mismatch"):
            self.execute()
        self.assertEqual(len((self.home / "launches").read_text().splitlines()), 1)
        ProcessBarrier(self.s.path, "addition").require_clear()

    def test_changed_output_or_cancelled_claim_cannot_adopt_saved_evidence(self):
        self.prepare()
        self.execute()
        output = self.folder / "output.json"
        original = output.read_text()
        body = json.loads(original)
        body["structured_output"] = {"summary": "An unrelated proposal"}
        output.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "does not match"):
            adopt(self.folder, self.context, self.task, self.s.path)
        output.write_text(original)
        self.s.control("addition", "cancel")
        with self.assertRaises(Conflict):
            adopt(self.folder, self.context, self.task, self.s.path)

    def test_missing_managed_workspace_records_fallback_before_any_session(self):
        self.prepare()
        receipt_path(WorkspaceCreationGate(self.s.path, "addition")).unlink()

        def fallback(launcher, *args, **kwargs):
            self.assertEqual(launcher["selection"]["reason"], "native_managed_workspace_required")
            self.assertEqual(launcher["selection"]["execution_mode"], "headless-compatibility")
            self.assertNotIn("interactive", kwargs)
            self.assertIn("-p", args[0])
            raise RuntimeError("Observed fallback before starting any provider")

        with patch("todo_flow.worker.spawn_terminal", side_effect=fallback):
            with self.assertRaisesRegex(RuntimeError, "Observed fallback"):
                self.execute()
        self.assertFalse((self.home / "launches").exists())
        self.assertFalse((self.s.path / ("interactive-task-" + self.task["id"] + ".json")).exists())
