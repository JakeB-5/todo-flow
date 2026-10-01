"""Real supervised processes and Unix sockets; synthetic Orca/model responses."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import unittest

import test_flow
from todo_flow.engine import Engine
from todo_flow.managed_workspace import receipt_path
from todo_flow.native_worker import preflight
from todo_flow.process_barrier import ProcessBarrier
from todo_flow.worker import codex_schema, run_worker
from todo_flow.worker_stop import worker_stop_summary
from todo_flow.workspace_creation import WorkspaceCreationGate


CODEX = r"""
import base64,hashlib,json,os,socket,struct,sys,time,tomllib,uuid
from pathlib import Path
root=Path(ROOT)
if sys.argv[1:] == ['--version']:
 (root/'version-probed').touch();print('codex-cli 9.9.9');sys.exit()
assert sys.argv[1]=='app-server'
home=Path(os.environ['CODEX_HOME'])
assert home == root/'auth'
settings=[sys.argv[i+1] for i,arg in enumerate(sys.argv) if arg=='-c']
assert 'sandbox_mode="read-only"' in settings and 'project_doc_max_bytes=0' in settings
assert 'features.plugins=false' in settings and 'features.hooks=false' in settings
projects=next(tomllib.loads(setting)['projects'] for setting in settings if setting.startswith('projects='))
assert projects[os.getcwd()]['trust_level']=='untrusted'
assert not any('cli_auth_credentials_store' in setting for setting in settings)
if not (root/'no-auth-file').exists():
 assert json.loads((home/'auth.json').read_text()) == {'synthetic':True}
with (root/'server-launches').open('a') as f:f.write('launch\n')
path=sys.argv[sys.argv.index('--listen')+1].removeprefix('unix://')
listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(path);listener.listen()
def connect_client():
 conn,_=listener.accept();buffer=bytearray()
 while b'\r\n\r\n' not in buffer:buffer.extend(conn.recv(4096))
 header=bytes(buffer).decode();key=[x.split(': ',1)[1] for x in header.split('\r\n') if x.startswith('Sec-WebSocket-Key:')][0]
 accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
 conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode())
 return conn
conn=connect_client()
def exact(n):
 result=b''
 while len(result)<n:
  chunk=conn.recv(n-len(result))
  if not chunk:sys.exit()
  result+=chunk
 return result
def receive():
 a,b=exact(2);n=b&127
 if n==126:n=struct.unpack('!H',exact(2))[0]
 elif n==127:n=struct.unpack('!Q',exact(8))[0]
 assert b&128
 mask=exact(4);data=exact(n)
 if a&15==10:return receive()
 return json.loads(bytes(x^mask[i%4] for i,x in enumerate(data)))
def send(obj):
 data=json.dumps(obj,ensure_ascii=False).encode();n=len(data)
 if n>100000:
  part=data[:1000];conn.sendall(bytes([1,126])+struct.pack('!H',len(part))+part)
  conn.sendall(bytes([137,1])+b'x')
  data=data[1000:];n=len(data);opcode=128
 else:opcode=129
 head=bytes([opcode,n if n<126 else 126 if n<65536 else 127])
 if n>=126:head+=struct.pack('!H' if n<65536 else '!Q',n)
 conn.sendall(head+data)
thread='thread-'+uuid.uuid4().hex;turn='turn-'+uuid.uuid4().hex
while True:
 msg=receive();method=msg['method']
 with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(msg)+'\n')
 if method=='initialize':
  send({'id':msg['id'],'result':{'codexHome':str(home),'platformFamily':'unix','platformOs':'test','userAgent':'synthetic'}})
 elif method=='initialized':pass
 elif method=='config/read':
  send({'id':msg['id'],'result':{'config':{'mcp_servers':{'fixture-tool':{'command':'must-not-run','env':{'TOKEN':'synthetic-secret'}}}}}})
 elif method=='thread/start':
  assert msg['params']['sandbox']=='read-only' and msg['params']['approvalPolicy']=='never'
  assert msg['params']['config']['mcp_servers']=={'fixture-tool':{'enabled':False}}
  send({'id':msg['id'],'result':{'thread':{'id':thread},'approvalPolicy':'never','sandbox':{'type':'readOnly','networkAccess':False},'cwd':msg['params']['cwd'],'instructionSources':[]}})
 elif method=='thread/read':
  assert msg['params']['threadId']==thread
  send({'id':msg['id'],'result':{'thread':{'id':thread}}})
 elif method=='thread/turns/list':
  assert msg['params']['threadId']==thread and msg['params']['itemsView']=='full'
  send({'id':msg['id'],'result':{'data':[{'id':turn,'status':'completed','error':None,'itemsView':'full','items':[item]}],'nextCursor':None}})
 elif method=='turn/start':
  assert msg['params']['threadId']==thread and msg['params']['outputSchema']['type']=='object'
  assert msg['params']['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
  if (root/'lose-response').exists():conn.close();sys.exit()
  proposal=json.loads((root/'proposal.json').read_text())
  item={'id':'message-one','type':'agentMessage','phase':'final_answer','text':json.dumps(proposal,ensure_ascii=False)}
  send({'method':'turn/started','params':{'threadId':thread,'turn':{'id':turn,'status':'inProgress','items':[]}}})
  send({'id':msg['id'],'result':{'turn':{'id':turn,'status':'inProgress','items':[]}}})
  if (root/'hang-after-accept').exists():time.sleep(60)
  if (root/'delayed-proposal').exists():time.sleep(1.5)
  if (root/'drop-after-accept').exists():
   conn.close();conn=connect_client();continue
  if (root/'silent-completion').exists():continue
  if (root/'delta-storm').exists():
   for i in range(5000):send({'method':'item/agentMessage/delta','params':{'threadId':thread,'turnId':turn,'delta':'x'}})
  send({'method':'item/completed','params':{'threadId':thread,'turnId':turn,'completedAtMs':123,'item':item}})
  send({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':turn,'status':'completed','error':None,'itemsView':'summary','items':[]}}})
"""
ORCA = r"""
import json,sys,shlex
from pathlib import Path
root=Path(ROOT);args=sys.argv[1:];meta={'runtimeId':'fixture-runtime'}
with (root/'orca-calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\n')
owned=json.loads((root/'owned.json').read_text())
terminal={'handle':'term-fixture','tabId':'tab-fixture','incarnationId':'incarnation-fixture','worktreeId':owned['id'],'executionHostId':'local','title':owned['title']}
if args[:2]==['worktree','show']:
 result={'worktree':owned}
elif args[:2]==['terminal','create']:
 command=args[args.index('--command')+1]
 argv=shlex.split(command)
 assert argv[0]=='exec' and argv[2].endswith('native_viewer.py')
 spec=json.loads(Path(argv[3]).read_text())
 assert spec['argv'][1]=='resume' and spec['argv'][2].startswith('thread-')
 assert '--remote' in spec['argv'] and '--sandbox' not in spec['argv']
 assert '--ask-for-approval' not in spec['argv']
 # This synthetic Orca has no UI; real projection behavior has separate tests.
 (Path(spec['folder'])/'native-sidebar.json').write_text(json.dumps({'status':'unconfirmed','error':'synthetic CLI fixture has no sidebar'}))
 (root/'viewer-closed').unlink(missing_ok=True)
 result={'terminal':terminal}
elif args[:2]==['terminal','show']:
 alive=not (root/'exited-viewer').exists()
 result={'terminal':{**terminal,'title':'workspace','connected':alive,'writable':alive,'orphaned':False}}
 if (root/'input-viewer').exists():result['terminal']['lastInputAt']=9999999999999
 if (root/'reuse-viewer').exists():result['terminal']['incarnationId']='foreign-incarnation'
elif args[:2]==['terminal','wait']:
 result={'wait':{'satisfied':True,'status':'exited'}}
elif args[:2]==['terminal','close']:
 (root/'viewer-closed').touch()
 result={'close':{'handle':'term-fixture','tabId':'tab-fixture','ptyKilled':not (root/'exited-viewer').exists()}}
 if (root/'kill-unconfirmed').exists():result['close']['ptyKilled']=False
elif args[:2]==['terminal','list']:
 rows=[] if (root/'viewer-closed').exists() else [terminal]
 if (root/'shared-viewer').exists():rows.append({**terminal,'handle':'foreign-pane','incarnationId':'foreign-incarnation'})
 result={'terminals':rows,'truncated':False,'totalCount':len(rows)}
else:raise AssertionError(args)
print(json.dumps({'ok':True,'result':result,'_meta':meta}))
"""


class NativeExecutionTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def prepare(self):
        self.s.start("addition")
        with self.s.transaction() as c:
            c.execute("UPDATE tasks SET kind='work' WHERE track=?", ("addition",))
        self.task = self.s.claim("native-fixture")
        self.engine = Engine(self.s)
        self.workspace = self.engine.ensure_workspace(self.task)
        self.context = self.engine.context(self.task, self.workspace)
        self.before_source = (self.workspace / "calc.py").read_bytes()
        self.fixture = self.repo.parent / "native-fixture"
        self.fixture.mkdir()
        self.bin = self.fixture / "bin"
        self.bin.mkdir()
        for name, source in (("codex", CODEX), ("orca", ORCA)):
            path = self.bin / name
            path.write_text(
                "#!" + sys.executable + "\n" + source.replace("ROOT", repr(str(self.fixture)))
            )
            path.chmod(0o700)
        self.auth = self.fixture / "auth"
        self.auth.mkdir()
        (self.auth / "auth.json").write_text(json.dumps({"synthetic": True}))
        owned = {
            "id": "repo-fixture::" + str(self.workspace),
            "instanceId": "workspace-fixture",
            "hostId": "local",
            "head": self.context["head"],
            "path": str(self.workspace),
        }
        owned["title"] = (
            f"TODO {self.task['track']} · {self.task['kind']} · {self.task['attempt'][-8:]}"
        )
        (self.fixture / "owned.json").write_text(json.dumps(owned))
        receipt = receipt_path(WorkspaceCreationGate(self.s.path, "addition"))
        receipt.write_text(json.dumps({"version": 1, "track": "addition", "observation": owned}))
        proposal = dict.fromkeys(codex_schema()["properties"])
        proposal.update(
            summary="합성 native worker",
            changes=[{"path": "calc.py", "content": "# 한글 제안\n" * 20000}],
        )
        (self.fixture / "proposal.json").write_text(json.dumps(proposal))
        self.launcher = {
            "backend": "orca",
            "cli": str(self.bin / "orca"),
            "worktree": "id:" + owned["id"],
            "repo": str(self.repo),
            "selection": {
                "reason": "fixture",
                "orca": {"advertised": {"custom_terminal_command": True}},
            },
        }
        self.config = {**self.engine.config, "worker": {"type": "codex"}, "worker_timeout": 15}
        self.env = patch.dict(
            os.environ,
            {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "CODEX_HOME": str(self.auth)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        hook = patch(
            "todo_flow.native_worker.status_hook", return_value=str(self.fixture / "hook.sh")
        )
        hook.start()
        self.addCleanup(hook.stop)

    def execute(self):
        with (
            self.engine.process_attempt(self.task),
            patch("todo_flow.worker.select_launcher", return_value=self.launcher),
        ):
            return run_worker(
                self.config,
                self.context,
                self.task,
                self.s.path,
                lambda pid: self.s.heartbeat(self.task, pid),
            )

    def test_real_supervised_native_roundtrip_preserves_long_proposal_and_cleanup(self):
        self.prepare()
        result = self.execute()
        self.assertEqual(result["summary"], "합성 native worker")
        summary = worker_stop_summary(self.s.path, self.task["track"], self.task["attempt"])
        row = next(row for row in summary["executions"] if row["proposal"] is not None)
        self.assertTrue(row["process"]["started"])
        self.assertTrue(row["process"]["group_exit_confirmed"])
        self.assertEqual(row["proposal"]["status"], "valid")
        self.assertTrue(row["proposal"]["validated"])
        self.assertEqual(row["receipts"]["native"]["record"]["status"], "complete")
        self.assertIsNone(row["goal_success"])
        self.assertEqual(result["changes"][0]["content"], "# 한글 제안\n" * 20000)
        self.assertEqual((self.workspace / "calc.py").read_bytes(), self.before_source)
        folder = self.s.path / "attempts" / self.task["attempt"]
        record = json.loads((folder / "native-session.json").read_text())
        self.assertEqual(record["status"], "complete")
        self.assertFalse((self.s.path / "terminal-slots.json").exists())
        self.assertFalse((folder / "native-home").exists())
        self.assertEqual(json.loads((self.auth / "auth.json").read_text()), {"synthetic": True})
        self.assertFalse(Path(record["socket"]).exists())
        ProcessBarrier(self.s.path, "addition").require_clear()
        calls = [
            json.loads(line)
            for line in (self.fixture / "orca-calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(sum(call[:2] == ["terminal", "create"] for call in calls), 1)
        self.assertEqual(sum(call[:2] == ["terminal", "close"] for call in calls), 1)
        requests = [
            json.loads(line) for line in (self.fixture / "requests.jsonl").read_text().splitlines()
        ]
        self.assertEqual(sum(request["method"] == "turn/start" for request in requests), 1)

    def test_thousands_of_deltas_do_not_delay_complete_proposal_adoption(self):
        self.prepare()
        self.config["worker_timeout"] = 8
        (self.fixture / "delta-storm").touch()
        self.assertEqual(self.execute()["summary"], "합성 native worker")
        ProcessBarrier(self.s.path, "addition").require_clear()

    def test_silent_completion_is_collected_without_another_worker_turn(self):
        self.prepare()
        self.config["worker_timeout"] = None
        (self.fixture / "silent-completion").touch()
        result = self.execute()
        self.assertEqual(result["summary"], "합성 native worker")
        folder = self.s.path / "attempts" / self.task["attempt"]
        record = json.loads((folder / "native-session.json").read_text())
        self.assertEqual(record["recovery_source"], "same-connection-full-history")
        self.assertTrue((folder / "native-recovered-turn.json").exists())
        requests = [
            json.loads(line) for line in (self.fixture / "requests.jsonl").read_text().splitlines()
        ]
        self.assertEqual(sum(r["method"] == "thread/start" for r in requests), 1)
        self.assertEqual(sum(r["method"] == "turn/start" for r in requests), 1)
        self.assertTrue(any(r["method"] == "thread/turns/list" for r in requests))

    def test_updated_codex_and_symlinked_large_auth_keep_native_execution(self):
        self.prepare()
        source = self.auth / "auth.json"
        original = source.read_text() + " " * 1_000_001
        target = self.auth / "linked-auth.json"
        target.write_text(original)
        source.unlink()
        source.symlink_to(target)
        self.assertEqual(self.execute()["summary"], "합성 native worker")
        self.assertFalse((self.fixture / "version-probed").exists())
        self.assertTrue(source.is_symlink())
        self.assertEqual(target.read_text(), original)
        folder = self.s.path / "attempts" / self.task["attempt"]
        self.assertEqual(
            json.loads((folder / "launch.json").read_text())["execution_mode"], "orca-native"
        )
        self.assertFalse((folder / "native-home").exists())

    def test_authentication_without_file_is_delegated_to_codex(self):
        self.prepare()
        (self.auth / "auth.json").unlink()
        (self.auth / "config.toml").write_text('cli_auth_credentials_store="keyring"\n')
        (self.fixture / "no-auth-file").touch()
        with patch.dict(os.environ):
            os.environ.pop("OPENAI_API_KEY", None)
            self.assertEqual(self.execute()["summary"], "합성 native worker")
        self.assertFalse((self.auth / "auth.json").exists())
        self.assertEqual(
            (self.auth / "config.toml").read_text(), 'cli_auth_credentials_store="keyring"\n'
        )
        folder = self.s.path / "attempts" / self.task["attempt"]
        self.assertEqual(
            json.loads((folder / "launch.json").read_text())["execution_mode"], "orca-native"
        )
        for path in folder.glob("native-request-*.json"):
            self.assertNotIn("synthetic-secret", path.read_text())

    def test_default_unlimited_worker_keeps_heartbeating_until_proposal(self):
        self.prepare()
        self.config.pop("worker_timeout")
        (self.fixture / "delayed-proposal").touch()
        with patch.object(self.s, "heartbeat", wraps=self.s.heartbeat) as heartbeat:
            self.assertEqual(self.execute()["summary"], "합성 native worker")
        self.assertGreaterEqual(heartbeat.call_count, 2)
        folder = self.s.path / "attempts" / self.task["attempt"]
        self.assertIsNone(json.loads((folder / "native-spec.json").read_text())["timeout"])
        ProcessBarrier(self.s.path, "addition").require_clear()

    def test_unlimited_worker_stops_when_claim_is_cancelled(self):
        self.prepare()
        self.config["worker_timeout"] = None
        (self.fixture / "hang-after-accept").touch()
        heartbeat = self.s.heartbeat

        def cancel_after_start(task, pid=None):
            if (self.fixture / "orca-calls.jsonl").exists():
                calls = (self.fixture / "orca-calls.jsonl").read_text()
                if '"terminal", "create"' in calls:
                    self.s.control("addition", "cancel")
            return heartbeat(task, pid)

        from todo_flow.store import Conflict

        with patch.object(self.s, "heartbeat", side_effect=cancel_after_start):
            with self.assertRaises(Conflict):
                self.execute()
        ProcessBarrier(self.s.path, "addition").require_clear()
        self.assertTrue((self.fixture / "viewer-closed").exists())

    def test_lost_turn_response_stops_and_never_launches_or_replays_a_viewer(self):
        self.prepare()
        (self.fixture / "lose-response").touch()
        with self.assertRaises(RuntimeError):
            self.execute()
        ProcessBarrier(self.s.path, "addition").require_clear()
        before = (self.fixture / "server-launches").read_text()
        import time

        # The same uncertain task must remain blocked even when a replacement
        # would otherwise choose another native capability or compatibility path.
        for mode in ("changed-version", "missing-auth", "headless", "command", "claude"):
            with self.subTest(mode=mode):
                self.task = {
                    **self.task,
                    "attempt": "replacement-" + mode,
                    "generation": self.task["generation"] + 1,
                }
                with self.s.transaction() as connection:
                    connection.execute(
                        "UPDATE tasks SET generation=? WHERE id=?",
                        (self.task["generation"], self.task["id"]),
                    )
                    connection.execute(
                        "INSERT INTO attempts(id,task,generation,status,started) VALUES(?,?,?,?,?)",
                        (
                            self.task["attempt"],
                            self.task["id"],
                            self.task["generation"],
                            "running",
                            time.time(),
                        ),
                    )
                if mode == "changed-version":
                    (self.fixture / mode).touch()
                if mode == "missing-auth":
                    (self.auth / "auth.json").unlink()
                if mode in ("command", "claude"):
                    self.config["worker"] = {"type": mode, "argv": ["must-not-run"]}
                self.context = self.engine.context(self.task, self.workspace)
                with (
                    self.engine.process_attempt(self.task),
                    patch(
                        "todo_flow.worker.select_launcher", return_value={"backend": "headless"}
                    ) as select,
                    patch("todo_flow.native_worker.preflight") as probe,
                    patch("todo_flow.worker.SupervisedProcess") as process,
                    self.assertRaises(FileExistsError),
                ):
                    run_worker(self.config, self.context, self.task, self.s.path, None)
                select.assert_not_called()
                probe.assert_not_called()
                process.assert_not_called()
        self.assertEqual((self.fixture / "server-launches").read_text(), before)
        calls = (self.fixture / "orca-calls.jsonl").read_text()
        self.assertNotIn('"terminal", "create"', calls)

    def test_unreadable_intent_shapes_block_before_launcher_selection(self):
        self.prepare()
        intent = self.s.path / ("native-task-" + self.task["id"] + ".json")
        for shape in ("malformed", "unknown-version", "directory", "dangling-symlink"):
            with self.subTest(shape=shape):
                if shape == "directory":
                    intent.mkdir()
                elif shape == "dangling-symlink":
                    intent.symlink_to(self.fixture / "absent")
                else:
                    intent.write_text("invalid" if shape == "malformed" else '{"version":999}')
                with (
                    patch("todo_flow.worker.select_launcher") as select,
                    self.assertRaises(FileExistsError),
                ):
                    run_worker(self.config, self.context, self.task, self.s.path, None)
                select.assert_not_called()
                if shape == "directory":
                    intent.rmdir()
                else:
                    intent.unlink()
        self.assertFalse((self.fixture / "server-launches").exists())

    def test_already_exited_viewer_can_be_retired_without_pty_kill(self):
        self.prepare()
        (self.fixture / "exited-viewer").touch()
        self.assertEqual(self.execute()["summary"], "합성 native worker")

    def test_prior_input_and_shared_tab_preserve_viewer(self):
        # Each case owns an isolated attempt; no real Orca/model calls.
        for marker in ("input-viewer", "shared-viewer"):
            with self.subTest(marker=marker):
                case = NativeExecutionTests()
                case.setUp()
                try:
                    case.prepare()
                    (case.fixture / marker).touch()
                    self.assertEqual(case.execute()["summary"], "합성 native worker")
                    calls = (case.fixture / "orca-calls.jsonl").read_text()
                    self.assertNotIn('"terminal", "close"', calls)
                finally:
                    case.doCleanups()
                    case.tearDown()

    def test_viewer_cleanup_failure_preserves_completed_proposal(self):
        self.prepare()
        (self.fixture / "kill-unconfirmed").touch()
        self.assertEqual(self.execute()["summary"], "합성 native worker")
        folder = self.s.path / "attempts" / self.task["attempt"]
        record = json.loads((folder / "native-session.json").read_text())
        self.assertEqual(record["status"], "complete")
        self.assertTrue(record["viewer_cleanup_error"])
        self.assertTrue((folder / "native-proposal.json").exists())

    def test_188_legacy_terminals_and_old_limits_do_not_block_native_worker(self):
        self.prepare()
        records = {}
        for number in range(188):
            folder = self.s.path / "attempts" / f"old-{number}"
            folder.mkdir(parents=True)
            path = folder / "launch.json"
            path.write_text(
                json.dumps(
                    {
                        "backend": "orca",
                        "status": "accepted",
                        "terminal": {"handle": f"old-{number}"},
                    }
                )
            )
            records[path] = path.read_bytes()
        ledger = self.s.path / "terminal-slots.json"
        ledger.write_text('{"version":999,"limits":{"concurrency":0},"old":"preserve"}')
        original_ledger = ledger.read_bytes()
        self.config.update(terminal_concurrency=0, terminal_idle_limit=0)
        self.assertEqual(self.execute()["summary"], "합성 native worker")
        self.assertEqual(ledger.read_bytes(), original_ledger)
        self.assertEqual(records, {path: path.read_bytes() for path in records})

    def test_missing_owned_workspace_is_prelaunch_compatibility_reason(self):
        self.prepare()
        receipt_path(WorkspaceCreationGate(self.s.path, "addition")).unlink()
        value, reason = preflight(self.config, self.context, self.task, self.s.path, self.launcher)
        self.assertIsNone(value)
        self.assertEqual(reason, "native_managed_workspace_required")
        self.assertFalse((self.fixture / "server-launches").exists())

    def test_reused_viewer_is_preserved_without_losing_completed_proposal(self):
        self.prepare()
        (self.fixture / "reuse-viewer").touch()
        self.assertEqual(self.execute()["summary"], "합성 native worker")
        ProcessBarrier(self.s.path, "addition").require_clear()
        calls = [
            json.loads(line)
            for line in (self.fixture / "orca-calls.jsonl").read_text().splitlines()
        ]
        self.assertFalse(any(call[:2] == ["terminal", "close"] for call in calls))
        folder = self.s.path / "attempts" / self.task["attempt"]
        self.assertTrue((folder / "native-proposal.json").exists())
        record = json.loads((folder / "native-session.json").read_text())
        self.assertEqual(record["status"], "complete")
        self.assertTrue(record["viewer_cleanup_error"])

    def test_review_starts_an_independent_server_and_thread_with_provenance(self):
        self.prepare()
        self.execute()
        implementation = json.loads(
            (self.s.path / "attempts" / self.task["attempt"] / "native-session.json").read_text()
        )
        self.s.finish(self.task, {"summary": "Synthetic implementation complete"})
        with self.s.transaction() as connection:
            self.s.enqueue(
                connection,
                "addition",
                "review",
                "Independent fixture review",
                "native-review-fixture",
            )
        self.task = self.s.claim("independent-reviewer")
        self.context = self.engine.context(self.task, self.workspace)
        owned = json.loads((self.fixture / "owned.json").read_text())
        owned["title"] = (
            f"TODO {self.task['track']} · {self.task['kind']} · {self.task['attempt'][-8:]}"
        )
        (self.fixture / "owned.json").write_text(json.dumps(owned))
        proposal = dict.fromkeys(codex_schema()["properties"])
        proposal.update(
            summary="Synthetic independent review",
            verdict="met",
            conditions=[{"id": "sum", "verdict": "met", "evidence": "Synthetic fixture only"}],
        )
        (self.fixture / "proposal.json").write_text(json.dumps(proposal))
        result = self.execute()
        self.assertEqual(result["verdict"], "met")
        folder = self.s.path / "attempts" / self.task["attempt"]
        review = json.loads((folder / "native-session.json").read_text())
        self.assertNotEqual(implementation["session"], review["session"])
        self.assertNotEqual(implementation["socket"], review["socket"])
        spec = json.loads((folder / "native-spec.json").read_text())
        self.assertIn(["local", implementation["session"]], spec["implementation_sessions"])
        self.assertEqual(
            (self.fixture / "server-launches").read_text().splitlines(), ["launch", "launch"]
        )

    def test_owned_connection_reconnects_through_full_history_without_resending(self):
        self.prepare()
        (self.fixture / "drop-after-accept").touch()
        result = self.execute()
        self.assertEqual(result["summary"], "합성 native worker")
        folder = self.s.path / "attempts" / self.task["attempt"]
        record = json.loads((folder / "native-session.json").read_text())
        self.assertEqual(record["recovery_source"], "same-server-full-history")
        self.assertEqual((self.fixture / "server-launches").read_text().splitlines(), ["launch"])
        requests = [
            json.loads(line) for line in (self.fixture / "requests.jsonl").read_text().splitlines()
        ]
        for method in ("thread/start", "turn/start"):
            self.assertEqual(sum(request["method"] == method for request in requests), 1)
        self.assertTrue(any(request["method"] == "thread/turns/list" for request in requests))
        ProcessBarrier(self.s.path, "addition").require_clear()

    def test_timeout_confirms_server_cleanup_and_does_not_return_a_proposal(self):
        self.prepare()
        self.config["worker_timeout"] = 2
        (self.fixture / "hang-after-accept").touch()
        with self.assertRaises(RuntimeError):
            self.execute()
        ProcessBarrier(self.s.path, "addition").require_clear()
        folder = self.s.path / "attempts" / self.task["attempt"]
        record = json.loads((folder / "native-session.json").read_text())
        self.assertIsInstance(record["server_exit"], int)
        self.assertFalse(Path(record["socket"]).exists())
        self.assertFalse((folder / "native-proposal.json").exists())
        self.assertFalse((folder / "native-home").exists())
        self.assertEqual(json.loads((self.auth / "auth.json").read_text()), {"synthetic": True})
