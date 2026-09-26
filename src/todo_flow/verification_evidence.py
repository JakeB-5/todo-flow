"""Require current verification evidence at publication and completion boundaries.

This read-only check never runs verification or upgrades legacy evidence. Call it
again after waiting for an effect lock, immediately before the guarded effect.
Ownership and independent review remain the caller's responsibility.
"""

import json

from . import verification_identity
from .adapters import command
from .checkout import require_clean
from .store import Conflict, fingerprint


def require_current(config, workspace, expected_head, evidence):
    """Return successful evidence for this clean candidate and declared inputs."""
    if not expected_head:
        raise Conflict("Current verification requires a registered candidate HEAD")
    head = require_clean(workspace, expected_head)
    tree = command(["git", "rev-parse", "HEAD^{tree}"], workspace)
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError as error:
            raise Conflict("Current verification evidence is invalid") from error
    if (
        not isinstance(evidence, dict)
        or evidence.get("ok") is not True
        or evidence.get("head") != head
        or evidence.get("tree") != tree
        or evidence.get("command") != config.get("verify")
        or evidence.get("key") != fingerprint([tree, config.get("verify")])
    ):
        raise Conflict("Current verification of the exact candidate is missing")
    try:
        current = verification_identity.capture(
            config, workspace, verification_identity.execution_environment()
        )
        if not verification_identity.matches(evidence.get("identity"), current):
            raise Conflict("Verification inputs changed; fresh verification is required")
    except verification_identity.VerificationIdentityError as error:
        raise Conflict("Current verification identity is unavailable: " + str(error)) from error
    require_clean(workspace, head)
    return evidence
