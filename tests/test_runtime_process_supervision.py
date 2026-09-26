import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_flow
from todo_flow.engine import Engine
from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError
from todo_flow.process_inventory import ProcessInventory
from todo_flow.store import Store


class RuntimeProcessSupervisionTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    def wait_for(self, predicate):
        deadline = time.monotonic() + 12
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for real runtime process")
            time.sleep(0.02)

    def exercise_driver_death(self, mode):
        self.s.start("addition")
        task = self.s.claim("crashing-driver")
        ready, writes = self.root / "ready", self.root / "writes"
        descendant = (
            "import signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"Path({str(ready)!r}).touch()\n"
            "while True:\n"
            f"    with open({str(writes)!r}, 'a') as f: f.write('x')\n"
            "    time.sleep(.01)\n"
        )
        worker = (
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable,'-c',{descendant!r}])\n"
            "time.sleep(60)\n"
        )
        driver_code = """
import json,sys
from todo_flow.engine import Engine
from todo_flow.store import Store
engine=Engine(Store(sys.argv[1]))
task=json.loads(sys.argv[2])
command=json.loads(sys.argv[3])
mode=sys.argv[4]
engine.config.update(worker={'type':'command','argv':command},worker_protocol=2,
                     worker_launcher='headless',worker_timeout=60)
if mode=='terminal':
    engine.config.update(worker_launcher='terminal', terminal_command=[sys.executable,'-c',
        'import subprocess,sys; subprocess.Popen(sys.argv[1],shell=True,start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)',
        '{command}'])
if mode=='verification':
    workspace=engine.ensure_workspace(task)
    engine.config.update(verify=command,verify_timeout=60)
    engine.verify(task,workspace)
else:
    engine.execute(task)
"""
        driver = subprocess.Popen(
            [
                sys.executable,
                "-c",
                driver_code,
                str(self.s.path),
                json.dumps(task),
                json.dumps([sys.executable, "-c", worker]),
                mode,
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: driver.poll() is None and driver.kill())
        self.wait_for(lambda: writes.exists() and writes.stat().st_size > 0)
        inventory = ProcessInventory(
            self.s.path, "addition", task["attempt"], task["id"], task["generation"]
        )
        self.assertEqual(len(inventory.read()["executions"]), 1)
        with self.assertRaises(ProcessBarrierError):
            ProcessBarrier(self.s.path, "addition").require_clear()
        driver.kill()
        driver.wait(timeout=5)

        def cleaned():
            try:
                ProcessBarrier(self.s.path, "addition").require_clear()
                return True
            except ProcessBarrierError:
                return False

        self.wait_for(cleaned)
        before = writes.read_bytes()
        time.sleep(0.15)
        self.assertEqual(before, writes.read_bytes(), "Descendant wrote after confirmation")
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        Engine(Store(self.s.path)).reconcile()
        with self.s.connect() as connection:
            recovered = connection.execute(
                "SELECT * FROM tasks WHERE id=?", (task["id"],)
            ).fetchone()
        self.assertEqual(recovered["status"], "queued")
        self.assertGreater(recovered["generation"], task["generation"])
        self.assertEqual(inventory.read()["state"], "sealed")
        with self.assertRaises(ProcessBarrierError):
            inventory.register()

    def test_headless_driver_sigkill_cleans_children_then_new_engine_recovers(self):
        self.exercise_driver_death("headless")

    def test_terminal_driver_sigkill_cleans_children_then_new_engine_recovers(self):
        self.exercise_driver_death("terminal")

    def test_verification_driver_sigkill_cleans_children_then_new_engine_recovers(self):
        self.exercise_driver_death("verification")

    def test_unknown_inventory_version_blocks_before_recovery_writes(self):
        self.s.start("addition")
        task = self.s.claim("driver")
        inventory = ProcessInventory(
            self.s.path, "addition", task["attempt"], task["id"], task["generation"]
        )
        inventory.start()
        value = inventory.read()
        value["version"] = 99
        inventory.path.write_text(json.dumps(value))
        before = inventory.path.read_bytes()
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        Engine(Store(self.s.path)).reconcile()
        self.assertEqual(inventory.path.read_bytes(), before)
        self.assertEqual(self.s.snapshot()["tasks"][0]["status"], "running")

    def test_driver_death_before_supervisor_dispatch_cancels_inventory_permit(self):
        self.s.start("addition")
        task = self.s.claim("driver")
        ready, unexpected = self.root / "ready", self.root / "unexpected"
        code = """
import json,sys,time
from pathlib import Path
from todo_flow.engine import Engine
from todo_flow.store import Store
from todo_flow import supervised_process
def stop_before_dispatch(*args,**kwargs):
    Path(sys.argv[3]).touch()
    time.sleep(60)
supervised_process.subprocess.Popen=stop_before_dispatch
engine=Engine(Store(sys.argv[1]))
# Use the worker boundary directly: git setup must not use the injected Popen.
from todo_flow.worker import run_worker
run_worker({'worker_protocol':2,'worker_launcher':'headless',
            'worker':{'type':'command','argv':[sys.executable,'-c',
            'from pathlib import Path; Path('+repr(sys.argv[4])+').touch()']}},
           {'workspace':sys.argv[5]},json.loads(sys.argv[2]),sys.argv[1],lambda pid:None)
"""
        driver = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(self.s.path),
                json.dumps(task),
                str(ready),
                str(unexpected),
                str(self.repo),
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: driver.poll() is None and driver.kill())
        self.wait_for(ready.exists)
        driver.kill()
        driver.wait(timeout=5)
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        Engine(Store(self.s.path)).reconcile()
        self.assertFalse(unexpected.exists())
        history = ProcessBarrier(self.s.path, "addition").history()
        self.assertEqual(history[-1]["evidence"]["outcome"], "not-spawned")
        self.assertEqual(self.s.snapshot()["tasks"][0]["status"], "queued")

    def test_terminal_delivered_after_driver_death_never_starts_worker(self):
        self.s.start("addition")
        task = self.s.claim("driver")
        delivery, unexpected = self.root / "delivery", self.root / "unexpected"
        code = """
import json,sys
from todo_flow.engine import Engine
from todo_flow.store import Store
engine=Engine(Store(sys.argv[1]))
engine.config.update(worker_protocol=2,worker_launcher='terminal',
    worker={'type':'command','argv':[sys.executable,'-c',
        'from pathlib import Path; Path('+repr(sys.argv[4])+').touch()']},
    terminal_command=[sys.executable,'-c',
        'import sys; from pathlib import Path; Path(sys.argv[1]).write_text(sys.argv[2])',
        sys.argv[3],'{command}'])
engine.execute(json.loads(sys.argv[2]))
"""
        driver = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(self.s.path),
                json.dumps(task),
                str(delivery),
                str(unexpected),
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: driver.poll() is None and driver.kill())
        self.wait_for(lambda: delivery.exists() and delivery.stat().st_size > 0)
        driver.kill()
        driver.wait(timeout=5)
        import shlex

        bridge = subprocess.run(
            shlex.split(delivery.read_text()), capture_output=True, text=True, timeout=5
        )
        self.assertNotEqual(bridge.returncode, 0, bridge.stdout + bridge.stderr)
        self.assertFalse(unexpected.exists(), "Late delivery started after its driver died")
        barrier = ProcessBarrier(self.s.path, "addition")
        barrier.require_clear()
        self.assertEqual(barrier.history()[-1]["evidence"]["outcome"], "not-spawned")
        with self.s.transaction() as connection:
            connection.execute("UPDATE tasks SET lease=0 WHERE id=?", (task["id"],))
        Engine(Store(self.s.path)).reconcile()
        self.assertEqual(self.s.snapshot()["tasks"][0]["status"], "queued")
