"""Temporary runtime homes for unittest fixtures and their child processes."""

import os
import tempfile
from pathlib import Path


def isolate_runtime_home(test_case):
    """Install before runtime work; unwind nested fixtures in reverse order.

    Independent concurrent fixtures belong in separate processes. Threads and
    children within one fixture inherit its home and must stop before cleanup.
    An inherited TODO_FLOW_HOME may be the caller's real installation, so always
    allocate a fresh home here. Explicit updater fixtures can still use their
    own temporary homes.
    """
    directory = tempfile.TemporaryDirectory(prefix="todo-flow-test-home-")
    test_case.addCleanup(directory.cleanup)
    previous = os.environ.get("TODO_FLOW_HOME")

    def restore():
        if previous is None:
            os.environ.pop("TODO_FLOW_HOME", None)
        else:
            os.environ["TODO_FLOW_HOME"] = previous

    test_case.addCleanup(restore)
    path = Path(directory.name).resolve()
    os.environ["TODO_FLOW_HOME"] = str(path)
    return path
