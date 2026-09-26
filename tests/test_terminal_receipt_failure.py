"""Receipt I/O failure must not abandon a bridge's live subprocess handle."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from todo_flow import terminal_worker, verification


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "Requires POSIX process groups")
class TerminalReceiptFailureTests(unittest.TestCase):
    def test_receipt_failure_still_stops_live_term_ignoring_descendants(self):
        for failed_status in ("running", "cleaning"):
            for open_pipes in (False, True):
                with (
                    self.subTest(failed_status=failed_status, open_pipes=open_pipes),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    folder = Path(temporary)
                    child = (
                        "import signal,time\n"
                        "from pathlib import Path\n"
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                        "Path('writes').write_text('ready\\n')\n"
                        "Path('ready').touch()\n"
                        "while True:\n"
                        "    with open('writes', 'a') as output: output.write('tick\\n')\n"
                        "    time.sleep(.01)\n"
                    )
                    redirect = (
                        "" if open_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
                    )
                    parent = (
                        "import subprocess,sys,time\n"
                        "from pathlib import Path\n"
                        f"subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})\n"
                        "while not Path('ready').exists(): time.sleep(.01)\n"
                    )
                    (folder / "input.json").write_text("{}")
                    spec = folder / "terminal-spec.json"
                    spec.write_text(
                        json.dumps(
                            {
                                "argv": [sys.executable, "-c", parent],
                                "cwd": temporary,
                                "title": "Receipt failure fixture",
                            }
                        )
                    )
                    # Inject in the actual bridge process after a live descendant
                    # is ready. A failed running write also fails all later writes.
                    bootstrap = """
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import terminal_worker

spec = Path(sys.argv[2])
folder = spec.parent
failed_status = sys.argv[3]
original_save = terminal_worker.save

def failing_save(path, value):
    status = value["status"]
    if status == "running":
        (folder / "fixture-pgid").write_text(str(value["pid"]))
        deadline = time.monotonic() + 10
        while not (folder / "ready").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Fixture descendant did not start")
            time.sleep(.01)
    if status == failed_status or (
        failed_status == "running" and status in ("cleaning", "cleanup_failed", "exited")
    ):
        raise OSError("injected receipt storage failure")
    original_save(path, value)

terminal_worker.save = failing_save
raise SystemExit(terminal_worker.main(str(spec)))
"""
                    bridge = subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            bootstrap,
                            str(Path(terminal_worker.__file__).parent),
                            str(spec),
                            failed_status,
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        start_new_session=True,
                    )
                    try:
                        _, stderr = bridge.communicate(timeout=20)
                        self.assertNotEqual(bridge.returncode, 0)
                        self.assertIn("injected receipt storage failure", stderr)
                        self.assertTrue((folder / "ready").exists())
                        pgid = int((folder / "fixture-pgid").read_text())
                        self.assertFalse(verification.group_running(pgid))
                        receipt = json.loads((folder / "terminal-process.json").read_text())
                        self.assertNotEqual(receipt["status"], "exited")
                        self.assertNotIn("cleanup_confirmed", receipt)
                        before = (folder / "writes").read_bytes()
                        time.sleep(0.1)
                        self.assertEqual((folder / "writes").read_bytes(), before)
                    finally:
                        # Only this test's fresh fixture group is eligible for disposal.
                        identity = folder / "fixture-pgid"
                        if identity.exists():
                            pgid = int(identity.read_text())
                            if verification.group_running(pgid):
                                try:
                                    os.killpg(pgid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                        if bridge.poll() is None:
                            bridge.kill()
                        bridge.communicate(timeout=5)
