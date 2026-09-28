import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from todo_flow.native_viewer import follow, projection, publish


class NativeViewerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hook = self.root / "hook.sh"
        self.hook.write_text('cat >> "$FIXTURE_HOOK_LOG"\nprintf "\\n" >> "$FIXTURE_HOOK_LOG"\n')
        self.cli = self.root / "orca"
        self.cli.write_text(
            "#!" + sys.executable + "\n"
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "assert sys.argv[1:]==['worktree','ps','--limit','1000','--json']\n"
            "events=[json.loads(x) for x in Path(os.environ['FIXTURE_HOOK_LOG']).read_text().splitlines()]\n"
            "last=events[-1]\n"
            "agent={'paneKey':'tab:pane','agentType':'codex','prompt':last['prompt'],"
            "'state':'done' if last['hook_event_name']=='Stop' else 'working'}\n"
            "rows=[] if os.environ.get('FIXTURE_MISSING') else [agent]\n"
            "print(json.dumps({'ok':True,'_meta':{'runtimeId':'runtime-one'},"
            "'result':{'worktrees':[{'worktreeId':'repo::/worker','agents':rows}]}}))\n"
        )
        self.cli.chmod(0o700)
        self.spec = {
            "task": {"id": "task", "generation": 1, "attempt": "attempt"},
            "session": "thread-one",
            "turn": "turn-one",
            "head": "head-one",
            "worktree": "repo::/worker",
            "workspace": "/worker",
            "title": "TODO fixture · work · attempt",
            "model": "fixture",
            "folder": str(self.root),
            "hook": str(self.hook),
            "cli": str(self.cli),
        }
        self.record = {**self.spec, "version": 1, "status": "viewer-accepted"}
        self.env = {
            "ORCA_WORKTREE_ID": "repo::/worker",
            "ORCA_PANE_KEY": "tab:pane",
            "ORCA_TAB_ID": "tab",
            "FIXTURE_HOOK_LOG": str(self.root / "events.jsonl"),
        }

    def test_host_bound_lifecycle_registers_sidebar_and_reports_returned_proposal(self):
        path = self.root / "native-session.json"
        path.write_text(json.dumps(self.record))
        stop = threading.Event()
        with patch.dict(os.environ, self.env):
            thread = threading.Thread(target=follow, args=(self.spec, stop))
            thread.start()
            try:
                self.await_state("working")
                path.write_text(json.dumps({**self.record, "status": "proposal-received"}))
                final = self.await_state("proposal-received")
            finally:
                stop.set()
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(final["status"], "confirmed")
        self.assertEqual(final["observation"]["agent"]["state"], "done")
        events = [
            json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [row["hook_event_name"] for row in events], ["SessionStart", "UserPromptSubmit", "Stop"]
        )
        self.assertTrue(all(row["session_id"] == "thread-one" for row in events))

    def await_state(self, state):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                record = json.loads((self.root / "native-sidebar.json").read_text())
                if record.get("state") == state:
                    return record
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        self.fail(f"No sidebar receipt for {state}")

    def test_changed_binding_and_terminal_cannot_publish_session_events(self):
        for field in ("task", "session", "turn", "head", "worktree"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                projection(self.spec, {**self.record, field: "foreign"})
        with self.assertRaises(ValueError):
            publish(self.spec, "working", {**self.env, "ORCA_WORKTREE_ID": "other"})
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_terminal_acceptance_or_hook_exit_does_not_prove_sidebar_registration(self):
        with patch.dict(os.environ, {**self.env, "FIXTURE_MISSING": "1"}):
            self.assertIsNone(publish(self.spec, "working", dict(os.environ)))

    def test_cancelled_group_is_stopped_without_claiming_a_proposal(self):
        self.assertEqual(
            projection(self.spec, {**self.record, "server_group_exit_confirmed": True}), "stopped"
        )
        self.assertIsNone(projection(self.spec, {**self.record, "status": "server-stopped"}))
