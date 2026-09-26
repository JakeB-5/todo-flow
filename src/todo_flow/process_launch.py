"""Serialize a durable spawn permit with cancellation of a delayed launch.

Only trusted supervisors may use this API. A consumed permit is NOT proof of
process ownership or exit. Once consumed, cancellation cannot confirm cleanup;
the owning supervisor must establish group exit through a separate contract.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import os

from .process_barrier import ProcessBarrier, ProcessBarrierError


class LaunchGate:
    def __init__(self, directory, track, attempt, execution):
        self.barrier = ProcessBarrier(directory, track)
        self.attempt = attempt
        self.execution = execution
        identity = (track, attempt, execution)
        if not all(isinstance(value, str) and value.strip() for value in identity):
            raise ValueError("A complete launch identity is required")
        # Length prefixes avoid ambiguous identities without restricting track IDs.
        key = hashlib.sha256(
            "".join(f"{len(value)}:{value}" for value in identity).encode("utf-8")
        ).hexdigest()
        self.path = self.barrier.directory / (key + ".launch")

    @classmethod
    def prepare(cls, directory, track, attempt, execution, *, backend):
        gate = cls(directory, track, attempt, execution)
        gate.barrier.begin(
            attempt,
            execution,
            reason="Launch permit prepared before dispatch",
            evidence={"backend": backend, "permit": str(gate.path)},
        )
        return gate

    def _event(self):
        events = [event for event in self.barrier.history() if event["execution"] == self.execution]
        if (
            not events
            or events[-1]["attempt"] != self.attempt
            or events[0]["evidence"].get("permit") != str(self.path)
        ):
            raise ProcessBarrierError("Launch permit has no matching durable intent")
        return events[-1]

    def _advance(self, event, state, reason, evidence):
        self.barrier.advance(
            self.attempt,
            self.execution,
            state,
            expected_revision=event["revision"],
            reason=reason,
            evidence=evidence,
        )
        return self._event()

    @contextmanager
    def _locked(self):
        # The inode is never replaced or unlinked. Independent drivers and late
        # terminal deliveries must contend on this same lock.
        try:
            with self.path.open("a+b", buffering=0) as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stream.seek(0)
                marker = stream.read()
                if marker not in (b"", b"launch\n", b"cancel\n"):
                    raise ProcessBarrierError(f"Interrupted launch permit: {self.path}")
                yield stream, marker
        except OSError as error:
            raise ProcessBarrierError(f"Cannot acquire/update launch permit {self.path}") from error

    def _mark(self, stream, marker):
        if stream.write(marker) != len(marker):
            raise ProcessBarrierError(f"Incomplete launch permit write: {self.path}")
        self._sync(stream)

    def _sync(self, stream):
        os.fsync(stream.fileno())
        directory_fd = os.open(self.barrier.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @contextmanager
    def launching(self):
        """Enclose exactly the synchronous spawn, before recording running.

        Callers retain their live process handle even if recording running fails,
        and must still attempt owned cleanup in a finally block. An interrupted
        or failed spawn remains unresolved; it is never guessed to be unspawned.
        """
        with self._locked() as (stream, marker):
            event = self._event()
            if marker or event["state"] != "intent":
                raise ProcessBarrierError("Launch permit was cancelled, consumed, or superseded")
            # Persist consumption BEFORE control reaches any spawning operation.
            self._mark(stream, b"launch\n")
            try:
                yield
                self._advance(
                    event,
                    "running",
                    "Synchronous spawn returned to its supervisor",
                    {"permit": str(self.path)},
                )
            except BaseException as error:
                current = self._event()
                if current["state"] in ("intent", "running", "cleaning"):
                    self._advance(
                        current,
                        "unknown",
                        "Spawn or its durable acknowledgement was interrupted",
                        {"permit": str(self.path), "error": type(error).__name__},
                    )
                raise

    def cancel_pending(self):
        """Prevent every later launch before confirming that none was started.

        Failure to acquire the lock is an unresolved launch, not exit evidence.
        A durable consumed marker cannot be cleared even if no PID was recorded.
        """
        with self._locked() as (stream, marker):
            event = self._event()
            if marker == b"launch\n":
                raise ProcessBarrierError("Consumed launch permit requires owned cleanup")
            if marker == b"":
                if event["state"] != "intent":
                    raise ProcessBarrierError("Missing launch permit cannot prove non-launch")
                self._mark(stream, b"cancel\n")
            # A previous cancellation may have stopped during fsync. Re-establish
            # durability before allowing a confirmation, including on recovery.
            self._sync(stream)
            if event["state"] == "confirmed":
                if event["evidence"].get("outcome") != "not-spawned":
                    raise ProcessBarrierError("Cancellation does not match exit evidence")
                return
            if event["state"] == "intent":
                event = self._advance(
                    event,
                    "cleaning",
                    "Durable cancellation prevents delayed dispatch",
                    {"permit": str(self.path)},
                )
            if event["state"] != "cleaning":
                raise ProcessBarrierError("Cannot confirm an uncertain launch by cancellation")
            self._advance(
                event,
                "confirmed",
                "Unconsumed launch permit cancelled under the exclusive launch lock",
                {
                    "identity": {
                        "track": self.barrier.track,
                        "attempt": self.attempt,
                        "execution": self.execution,
                    },
                    "outcome": "not-spawned",
                    "proof": "Exclusive launch lock and durable unconsumed cancellation marker",
                    "permit": str(self.path),
                },
            )
