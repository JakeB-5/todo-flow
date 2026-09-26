"""Durable one-shot gate for a future Engine managed-workspace adapter.

This is not a route selector or a workspace validator. The host must retain its
metadata lock and claim fencing, validate the returned checkout before registering
it, and propagate WorkspaceCreationBlocked without Git fallback. Call require_clear
before route selection when no independently validated ownership permits recovery.
Existing intent or response evidence always requires reconciliation. Neither a new
worker claim nor a new selection request authorizes another create.
"""

import hashlib
import json
import os
from pathlib import Path


class WorkspaceCreationBlocked(RuntimeError):
    """Creation may have happened; do not create again or fall back."""


def _write_exclusive(path, value):
    payload = (json.dumps(value, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class WorkspaceCreationGate:
    def __init__(self, directory, track):
        if not isinstance(track, str) or not track.strip():
            raise ValueError("A track identity is required")
        self.track = track
        # Use a track key, not an attempt key: a replacement worker must see it.
        key = hashlib.sha256(track.encode("utf-8")).hexdigest()
        self.intent = Path(directory) / ("workspace-create-" + key + ".json")
        self.response = self.intent.with_suffix(".response.json")

    def require_clear(self):
        """Reject all existing evidence without parsing or changing it.

        The host must hold the metadata lock across this check and route selection.
        This check is not an ownership receipt or an atomic creation reservation;
        create_once still acquires its intent exclusively before transport.
        """
        for path in (self.intent, self.response):
            try:
                # lstat also detects dangling symlinks and non-file evidence.
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise WorkspaceCreationBlocked(
                    f"Creation evidence cannot be inspected; reconcile {path}"
                ) from error
            raise WorkspaceCreationBlocked(f"Creation evidence requires reconciliation: {path}")

    def create_once(
        self, task, *, request, repo, base, argv, assert_claim, create, creation_pins=None
    ):
        """Persist exact host intent before invoking create(argv).

        directory must already exist durably. request is a local correlation
        identity, not an Orca idempotency token. create is an injected transport;
        no undocumented CLI arguments or response fields are assumed here.
        Saved responses are evidence only, never proof of checkout ownership.
        """
        claim = {
            key: task[key]
            for key in ("id", "attempt", "track", "owner", "generation", "input_revision")
        }
        if claim["track"] != self.track:
            raise ValueError("Wrong track")
        for value in (request, repo, base, claim["id"], claim["attempt"], claim["owner"]):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Incomplete creation identity")
        for key in ("generation", "input_revision"):
            if type(claim[key]) is not int or claim[key] < 1:
                raise ValueError("Invalid claim revision")
        if not isinstance(argv, list) or not argv:
            raise ValueError("Creation argv is required")
        if not all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv):
            raise ValueError("Invalid creation argv")
        intent = {
            "version": 1,
            "state": "intent",
            "track": self.track,
            "request": request,
            "claim": claim,
            "repo": repo,
            "base": base,
            "argv": list(argv),
        }
        if creation_pins is not None:
            # Import locally: the observation validator also uses this module's
            # blocking error. Legacy opaque intents remain version 1 and cannot
            # be interpreted as independently pinned creation evidence.
            from .orca_adoption import creation_argv, validate_creation_pins

            pins = json.loads(json.dumps(creation_pins, allow_nan=False))
            validate_creation_pins(pins)
            if (
                repo != pins["repo_path"]
                or base != pins["base"]
                or len(argv) != 13
                or argv != creation_argv(argv[0], pins, argv[6])
            ):
                raise ValueError("Creation command does not match host pins")
            intent.update(version=2, creation_pins=pins)
        intent_digest = hashlib.sha256(
            json.dumps(intent, allow_nan=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert_claim()
        self.require_clear()
        try:
            _write_exclusive(self.intent, intent)
        except OSError as error:
            raise WorkspaceCreationBlocked(
                f"Creation intent cannot be acquired; reconcile {self.intent}"
            ) from error
        # Recheck after durable IO. Failure leaves an intentionally blocking intent.
        assert_claim()
        try:
            response = create(list(argv))
            _write_exclusive(
                self.response,
                {
                    "version": intent["version"],
                    "request": request,
                    "claim": claim,
                    "response": response,
                    **({"intent_sha256": intent_digest} if creation_pins is not None else {}),
                },
            )
        except Exception as error:
            raise WorkspaceCreationBlocked(
                f"Creation outcome requires reconciliation: {self.intent}"
            ) from error
        # Preserve the response even if the initiating claim expired in transport.
        assert_claim()
        return response
