"""Durable references and bounded reads for file-backed verification output.

These receipts describe output, not process ownership or verification success.
Only the existing supervisor can confirm termination. Readers never complete a
receipt after a driver crash, infer missing originals from legacy tails, or
accept paths supplied by a receipt without checking their canonical location.
"""

import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import time

from .maintenance import write_json
from .store import fingerprint


TAIL_BYTES = 4096
MAX_READ_BYTES = 16384
METADATA_BYTES = 32768
STREAMS = ("stdout", "stderr")


class VerificationLogError(RuntimeError):
    """Output storage could not be established or sealed."""


class UnsupportedLogFormat(ValueError):
    """Do not interpret or rewrite an unknown output format."""


def reference(identity):
    root = Path(identity["directory"]).resolve()
    names = {key: identity[key] for key in ("track", "attempt", "execution")}
    if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in names.values()):
        raise ValueError("Invalid verification log identity")
    attempt = names["attempt"]
    if attempt in (".", "..") or Path(attempt).name != attempt or "\x00" in attempt:
        raise ValueError("Invalid verification attempt path")
    # Standalone callers can reuse an execution name on different tracks.
    # Namespace the existing process-output files by the whole launch identity.
    folder = root / "process-output" / fingerprint(names)
    return {"version": 1, **names, "manifest": str(folder / "logs.json")}


def _path(directory, path):
    root = Path(directory).resolve()
    path = Path(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError as error:
        raise ValueError("Verification log path escapes state") from error
    if not parts or ".." in parts:
        raise ValueError("Invalid verification log path")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Verification log path contains a symlink")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Verification log path escapes state")
    return path


def _mkdir(directory, path):
    root = Path(directory).resolve()
    path = _path(root, path)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        _path(root, current)
        current.mkdir(exist_ok=True, mode=0o700)


def _open(directory, path):
    path = _path(directory, path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Verification output is not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _read_json(directory, path):
    with _open(directory, path) as stream:
        data = stream.read(METADATA_BYTES + 1)
    if len(data) > METADATA_BYTES:
        raise ValueError("Verification log metadata exceeds its read limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid verification log metadata")
    return value


def _write(directory, path, value):
    path = _path(directory, path)
    _mkdir(directory, path.parent)
    write_json(path, value)


def _load(directory, ref):
    if not isinstance(ref, dict) or ref.get("version") != 1:
        raise UnsupportedLogFormat("Unsupported verification log reference")
    try:
        expected = reference({"directory": directory, **{key: ref[key] for key in (
            "track", "attempt", "execution"
        )}})
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid verification log reference") from error
    if ref != expected:
        raise ValueError("Verification log reference does not match its identity")
    record = _read_json(directory, ref["manifest"])
    if record.get("version") != 1:
        raise UnsupportedLogFormat("Unsupported verification log manifest")
    if (
        record.get("reference") != ref
        or record.get("phase") not in ("running", "closed", "partial", "error")
        or not isinstance(record.get("files"), dict)
        or type(record.get("complete")) is not bool
    ):
        raise ValueError("Invalid verification log manifest")
    return record


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _range(directory, ref, record, stream, offset, limit):
    path = Path(ref["manifest"]).parent / (stream + ".log")
    with _open(directory, path) as source:
        before = _stamp(os.fstat(source.fileno()))
        size = before[2]
        start = max(0, size - limit) if offset is None else min(offset, size)
        source.seek(start)
        data = source.read(min(limit, size - start))
        after = _stamp(os.fstat(source.fileno()))
    complete = (
        record["phase"] == "closed"
        and record["complete"]
        and record["files"].get(stream) == before == after
    )
    return {
        "reference": ref,
        "stream": stream,
        "path": str(path),
        "start": start,
        "end": start + len(data),
        "size": size,
        "truncated": start > 0 or start + len(data) < size,
        "complete": complete,
        "text": data.decode("utf-8", errors="replace"),
        "textEncoding": "utf-8-replace",
        # Byte offsets may split a multibyte character. This is the lossless
        # representation for callers reconstructing the original byte stream.
        "base64": base64.b64encode(data).decode("ascii"),
    }


def read_range(directory, ref, stream, offset=0, limit=TAIL_BYTES):
    """Read at most MAX_READ_BYTES; never accept a caller-supplied file path."""
    if stream not in STREAMS:
        raise ValueError("Unknown verification output stream")
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= MAX_READ_BYTES:
        raise ValueError(f"offset must be nonnegative; limit must be 1..{MAX_READ_BYTES}")
    return _range(directory, ref, _load(directory, ref), stream, offset, limit)


def _termination(directory, ref):
    from .process_barrier import ProcessBarrierError
    from .process_launch import LaunchGate

    try:
        event = LaunchGate(
            directory, ref["track"], ref["attempt"], ref["execution"]
        )._event()
        evidence = event["evidence"]
        return {
            "state": event["state"],
            "completion": evidence.get("completion"),
            "returncode": evidence.get("returncode"),
        }
    except (OSError, ValueError, ProcessBarrierError) as error:
        return {"state": "unknown", "diagnostic": str(error)[:1000]}


def describe(directory, ref):
    """Return bounded tails and current availability without changing receipts."""
    if ref is None:
        return {"format": "legacy-tail-only", "complete": False, "reference": None}
    try:
        record = _load(directory, ref)
    except UnsupportedLogFormat as error:
        return {"format": "unsupported", "complete": False, "diagnostic": str(error)}
    except (OSError, ValueError, TypeError) as error:
        return {"format": "unavailable", "complete": False, "diagnostic": str(error)[:1000]}
    streams = {}
    for name in STREAMS:
        try:
            value = _range(directory, ref, record, name, None, TAIL_BYTES)
            value.pop("base64")
            value.pop("reference")
            streams[name] = value
        except (OSError, ValueError) as error:
            streams[name] = {"complete": False, "diagnostic": str(error)[:1000]}
    termination = _termination(directory, ref)
    complete = record["complete"] and all(value["complete"] for value in streams.values())
    phase = record["phase"]
    if phase == "closed" and not complete:
        phase = "partial"
    elif phase == "running" and termination["state"] == "confirmed":
        phase = "interrupted"
    return {
        "format": "file-backed-v1",
        "reference": ref,
        "phase": phase,
        "complete": complete,
        "started": record["started"],
        "finished": record.get("finished"),
        "diagnostic": record.get("diagnostic"),
        "termination": termination,
        "streams": streams,
        "maxReadBytes": MAX_READ_BYTES,
    }


def latest(directory, track):
    """Find the latest started verification even without an Engine result."""
    path = Path(directory).resolve() / "verification-logs" / (fingerprint(track) + ".json")
    try:
        ref = _read_json(directory, path)
        if ref.get("track") != track:
            raise ValueError("Verification log pointer belongs to another track")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return {"format": "unavailable", "complete": False, "diagnostic": str(error)[:1000]}
    return describe(directory, ref)


class LogCapture:
    """Keep the supervisor's output files; persist references before launching."""

    def __init__(self, identity):
        self.directory = Path(identity["directory"]).resolve()
        self.reference = reference(identity)
        self.folder = Path(self.reference["manifest"]).parent
        self.resources = ExitStack()
        self.streams = {}
        self.finished = False
        self.record = {
            "version": 1,
            "reference": self.reference,
            "phase": "running",
            "complete": False,
            "started": time.time(),
            "files": {},
        }

    def __enter__(self):
        try:
            _mkdir(self.directory, self.folder.parent)
            _path(self.directory, self.folder).mkdir(mode=0o700)
            _write(self.directory, self.reference["manifest"], self.record)
            attempt = self.directory / "attempts" / self.reference["attempt"]
            _write(self.directory, attempt / "verification-logs.json", self.reference)
            latest_path = (
                self.directory
                / "verification-logs"
                / (fingerprint(self.reference["track"]) + ".json")
            )
            _write(self.directory, latest_path, self.reference)
            for name in STREAMS:
                path = _path(self.directory, self.folder / (name + ".log"))
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                self.streams[name] = self.resources.enter_context(os.fdopen(descriptor, "w+b"))
            return self
        except (OSError, ValueError) as error:
            self.resources.close()
            self._fail(error)
            raise VerificationLogError("Cannot prepare verification output: " + str(error)) from error

    def _fail(self, error):
        self.record.update(phase="error", complete=False, diagnostic=str(error)[:1000])
        try:
            _write(self.directory, self.reference["manifest"], self.record)
        except (OSError, ValueError):
            # Disk failure may also prevent a diagnostic write. The last durable
            # receipt remains incomplete; no success is reconstructed from it.
            pass

    def _seal(self):
        files = {}
        for name, stream in self.streams.items():
            os.fsync(stream.fileno())
            observed = _stamp(os.fstat(stream.fileno()))
            with _open(self.directory, self.folder / (name + ".log")) as current:
                if _stamp(os.fstat(current.fileno())) != observed:
                    raise ValueError("Verification output was replaced before sealing")
            files[name] = observed
        return files

    def finish(self, event, returncode):
        if event["state"] != "confirmed":
            raise VerificationLogError("Output cannot be sealed without confirmed process exit")
        try:
            files = self._seal()
            completion = event["evidence"].get("completion")
            # Failed and interrupted producers may have encountered short writes.
            # Preserve their bytes without asserting complete producer output.
            complete = returncode == 0 and completion == "leader-exited"
            self.record.update(
                phase="closed" if complete else "partial",
                complete=complete,
                files=files,
                finished=time.time(),
                diagnostic=None if complete else "Verifier output may be partial",
            )
            _write(self.directory, self.reference["manifest"], self.record)
            self.finished = True
        except (OSError, ValueError) as error:
            self._fail(error)
            raise VerificationLogError("Cannot seal verification output: " + str(error)) from error
        return describe(self.directory, self.reference)

    def __exit__(self, kind, value, traceback):
        try:
            self.resources.close()
        finally:
            if not self.finished:
                self._fail(value or "Log capture ended without a final receipt")
        return False
