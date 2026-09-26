"""Driver-side handle: a private lease delegates cleanup to a surviving owner."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .process_barrier import ProcessBarrierError
from .process_inventory import note_prepared
from .process_launch import LaunchGate
from .verification import VerificationCleanupError


class SupervisedProcess:
    def __init__(self, argv, *, identity, cwd, stdin, stdout, stderr, timeout, env=None):
        self.gate = LaunchGate.prepare(**identity, backend="supervised")
        note_prepared(identity)
        self.returncode = None
        self._lease = None
        self._proc = None
        reader, writer = os.pipe()
        self._lease = writer
        spec = {
            "argv": argv,
            "identity": identity,
            "lease_fd": reader,
            "cwd": str(cwd),
            "timeout": timeout,
        }
        try:
            self._proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("process_supervisor.py")),
                    json.dumps(spec),
                ],
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=env,
                pass_fds=(reader,),
                start_new_session=True,
            )
        except BaseException:
            self._close_lease()
            # Even an uncertain Popen failure cannot deliver a second command:
            # cancellation races with the same durable launch gate.
            self.gate.cancel_pending()
            raise
        finally:
            os.close(reader)

    def __del__(self):
        if getattr(self, "_lease", None) is not None:
            try:
                self._close_lease()
            except OSError:
                pass

    @property
    def pid(self):
        return self._proc.pid

    def _close_lease(self):
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None

    def poll(self):
        code = self._proc.poll()
        if code is None:
            return None
        self._close_lease()
        try:
            event = self.gate._event()
            if event["state"] != "confirmed":
                raise ProcessBarrierError("Supervisor exited without cleanup confirmation")
        except (OSError, ProcessBarrierError) as error:
            raise VerificationCleanupError(str(error)) from error
        self.returncode = code
        return code

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("process supervisor", timeout)
            time.sleep(0.02)
        return self.returncode

    def stop(self, timeout=15):
        self._close_lease()
        try:
            self.wait(timeout)
        except subprocess.TimeoutExpired as error:
            raise VerificationCleanupError(
                f"Supervisor still owns unresolved cleanup; inspect {self.gate.barrier.path}"
            ) from error
