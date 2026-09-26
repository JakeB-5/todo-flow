"""Versioned identity for explicitly declared verification inputs.

Relative file names are resolved against the verification workspace. Call capture
before execution and again before accepting success. Pass the same environment
mapping to capture and the subprocess when integrating this module.

Metadata participates in identity so ordinary replace-and-restore writes invalidate
an observation even when the final bytes match. These observations are not an
immutable snapshot or a defense against privileged metadata manipulation. Undeclared
inputs, directory contents, transitive dependencies and remote services are not
inferred. Environment digests omit plaintext but are not password protection.
"""

import hashlib
import json
import math
import os
import stat
from pathlib import Path

from .release import VERIFICATION_IDENTITY_VERSION, validate_verify_identity

VERSION = VERIFICATION_IDENTITY_VERSION


class VerificationIdentityError(ValueError):
    """Identity is unsupported, invalid or cannot be observed consistently."""


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def validate(config):
    """Return a detached declaration using the standalone compatibility contract."""
    try:
        return validate_verify_identity(config)
    except ValueError as error:
        raise VerificationIdentityError(str(error)) from error


def execution_environment(environ=None):
    """Return the actual verifier environment, including the host override."""
    return {**(os.environ if environ is None else environ), "GIT_TERMINAL_PROMPT": "0"}


def _stamp(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def _file_digest(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _file_identity(name, workspace):
    path = Path(name)
    if not path.is_absolute():
        path = Path(workspace) / path
    try:
        resolved = path.resolve(strict=True)
        # Avoid blocking if a declared input has become a FIFO before open.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise VerificationIdentityError("Verification input must be a regular file")
            digest = _file_digest(stream)
            after = os.fstat(stream.fileno())
        if (
            _stamp(before) != _stamp(after)
            or _stamp(after) != _stamp(path.stat())
            or resolved != path.resolve(strict=True)
        ):
            raise VerificationIdentityError("Verification input changed while reading identity")
    except (OSError, RuntimeError) as error:
        raise VerificationIdentityError("Cannot read declared verification input") from error
    return {
        "path": name,
        "resolved": str(resolved),
        "sha256": digest,
        "stat": _stamp(after),
    }


def capture(config, workspace, environ=None):
    """Capture evidence without storing selected environment values or nonce text.

    Read failures and unsupported formats fail closed. Callers must not fall back
    to the older HEAD/argv-only cache when capture fails.
    """
    declaration = validate(config)
    argv = config.get("verify")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise VerificationIdentityError("Verification command must be a nonempty argv array")
    if not argv[0]:
        raise VerificationIdentityError("Verification executable must not be empty")
    timeout = config.get("verify_timeout", 180)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise VerificationIdentityError("Verification timeout must be positive and finite")
    environment = execution_environment(environ)
    files = [_file_identity(name, workspace) for name in declaration["files"]]
    # A second observation detects changes to early inputs while later ones were read.
    if files != [_file_identity(name, workspace) for name in declaration["files"]]:
        raise VerificationIdentityError("Verification inputs changed while capturing identity")
    return {
        "version": VERSION,
        "command": list(argv),
        "timeout": timeout,
        "files": files,
        "environment": [
            {
                "name": name,
                "sha256": _digest([name in environment, environment.get(name)]),
            }
            for name in declaration["environment"]
        ],
        "nonce_sha256": _digest(declaration["nonce"]),
    }


def matches(previous, current):
    """Compare nested identity evidence, refusing unsupported evidence versions.

    Pass record.get('identity') for previous. Legacy records without identity are
    cache misses; explicitly present unsupported formats must never be downgraded.
    HEAD, tree, clean checkout and successful execution remain caller obligations.
    """
    for identity in (previous, current):
        if identity is None:
            continue
        if (
            not isinstance(identity, dict)
            or type(identity.get("version")) is not int
            or identity["version"] != VERSION
        ):
            raise VerificationIdentityError("Unsupported verification identity evidence version")
    if current is None:
        raise VerificationIdentityError("Current verification identity is required")
    return previous is not None and previous == current
