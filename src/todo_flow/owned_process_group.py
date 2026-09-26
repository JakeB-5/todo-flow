"""Pin a dedicated group's PID until its last live member exits.

Only this object may reap its direct child. Do not install a SIGCHLD reaper,
call waitpid(-1), or pass its private Popen to other code. This is a live-driver
ownership primitive, not permission to reconstruct ownership from stored PIDs.
Callers must journal intent before spawn and retain this object on cleanup
failure. Driver-death recovery still needs a separately surviving supervisor.

leader_exited() observes zombies without reaping them. The unreaped session
leader pins the PID/PGID even after its command exits. No group lookup or signal
is permitted once that pin has been released.
"""

import os
import signal
import subprocess
import threading
import time

from .verification import VerificationCleanupError


class OwnedProcessGroup:
    def __init__(self, argv, **options):
        if "start_new_session" in options or "process_group" in options:
            raise ValueError("OwnedProcessGroup owns session creation")
        self._driver = os.getpid()
        self._lock = threading.Lock()
        self._sealed = False
        self._confirmed = False
        self._require_child_retention()
        self._proc = subprocess.Popen(argv, start_new_session=True, **options)

    @property
    def pid(self):
        return self._proc.pid

    @property
    def stdin(self):
        return self._proc.stdin

    @property
    def stdout(self):
        return self._proc.stdout

    @property
    def stderr(self):
        return self._proc.stderr

    def _require_child_retention(self):
        if os.getpid() != self._driver:
            raise VerificationCleanupError("Group ownership cannot transfer through fork")
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise VerificationCleanupError("Group ownership requires the default SIGCHLD handler")

    def _snapshot(self):
        self._require_child_retention()
        if self._sealed or self._proc.returncode is not None:
            raise VerificationCleanupError("Group ownership pin has already been released")
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,pgid=,stat="],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise VerificationCleanupError("Cannot inspect owned process group") from error
        if result.returncode or not result.stdout.strip():
            raise VerificationCleanupError("Cannot inspect owned process group")
        members = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if (
                len(fields) != 3
                or not fields[0].isdigit()
                or not fields[1].isdigit()
                or int(fields[0]) in members
            ):
                raise VerificationCleanupError("Invalid owned process group inspection")
            members[int(fields[0])] = (int(fields[1]), fields[2])
        leader = members.get(self.pid)
        if leader is None or leader[0] != self.pid:
            raise VerificationCleanupError("Owned session leader is missing or has changed group")
        live = any(
            pgid == self.pid and not state.startswith("Z") for pgid, state in members.values()
        )
        return leader[1].startswith("Z"), live

    def leader_exited(self):
        """Observe command completion without releasing its PID."""
        with self._lock:
            self._require_child_retention()
            if self._sealed:
                return True
            exited, _ = self._snapshot()
            return exited

    def _send(self, sig):
        if not self._snapshot()[1]:
            return
        # No wait/poll/communicate can release the PID between inspection and
        # signal. A concurrent leader exit still leaves the unreaped PID pinned.
        self._require_child_retention()
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError) as error:
            if self._snapshot()[1]:
                raise VerificationCleanupError(
                    f"Cannot send {sig.name} to owned process group {self.pid}"
                ) from error
        except OSError as error:
            raise VerificationCleanupError(
                f"Cannot send {sig.name} to owned process group {self.pid}"
            ) from error

    def _wait_for_group(self, timeout):
        deadline = time.monotonic() + timeout
        while self._snapshot()[1]:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True

    def stop(self):
        """Stop live members, then reap exactly once and return the command code.

        Pipes belong to the caller; readers must be bounded and joined before
        publishing output. Inspection or signal failure retains the pin for a
        retry by this same supervisor, and is never an exit receipt.
        """
        with self._lock:
            self._require_child_retention()
            if self._confirmed:
                return self._proc.returncode
            if not self._sealed:
                self._send(signal.SIGTERM)
                if not self._wait_for_group(0.3):
                    self._send(signal.SIGKILL)
                    if not self._wait_for_group(2):
                        raise VerificationCleanupError("Owned process group did not stop")
                # Seal BEFORE any operation can reap. Interrupted waits may
                # already have reaped; retries must never signal the PGID again.
                self._sealed = True
            try:
                code = self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise VerificationCleanupError("Cannot reap owned session leader") from error
            self._confirmed = True
            return code
