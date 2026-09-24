"""Check the actual checkout and index at proposal and evidence boundaries."""

from .adapters import command
from .store import Conflict, fingerprint


def require_clean(workspace, expected_head=None):
    head = command(["git", "rev-parse", "HEAD"], workspace)
    if expected_head is not None and head != expected_head:
        raise Conflict("Checkout HEAD changed; evidence must refer to the exact candidate")
    if command(["git", "status", "--porcelain=v1", "--untracked-files=all"], workspace):
        raise Conflict("Checkout has existing changes; preserve and inspect them before proceeding")
    return head


def snapshot(workspace):
    # Index blobs/modes plus worktree diff detect edits even when porcelain status is unchanged.
    return fingerprint(
        [
            command(["git", "rev-parse", "HEAD"], workspace),
            command(["git", "ls-files", "--stage", "-z"], workspace),
            command(["git", "diff", "--no-ext-diff", "--no-textconv", "--binary"], workspace),
            command(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], workspace),
        ]
    )


def require_snapshot(workspace, expected):
    if not expected or snapshot(workspace) != expected:
        raise Conflict(
            "Prepared merge checkout/index changed; preserve existing changes for inspection"
        )
