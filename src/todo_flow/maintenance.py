"""Cooperative process guards, kept outside package installations and project data.

This module uses only the standard library so a copied recovery runner can use it
while the installed package is being replaced.
"""

import contextlib
import fcntl
import functools
import hashlib
import json
import os
import tempfile
from pathlib import Path


def home():
    root = (
        Path(
            os.environ.get(
                "TODO_FLOW_HOME",
                Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "todo-flow",
            )
        )
        .expanduser()
        .resolve()
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def lease(path, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as file:
        try:
            fcntl.flock(file, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "An active runtime or update owns this resource. Stop the relevant driver/dashboard and retry."
            ) from error
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def project_key(state):
    return hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()


def project_lock(state):
    return home() / "projects" / project_key(state) / "runtime.lock"


@contextlib.contextmanager
def runtime_guard(state=None):
    with lease(home() / "runtime.lock"):
        if (home() / "engine-pending.json").exists():
            raise RuntimeError(
                "An engine update was interrupted. Run todo-flow upgrade --recover, or the saved recovery runner."
            )
        if state is None:
            yield
        else:
            state = Path(state).resolve()
            with lease(project_lock(state)):
                write_json(
                    home() / "projects" / project_key(state) / "project.json", {"state": str(state)}
                )
                yield


def guarded(function):
    @functools.wraps(function)
    def wrapper(owner, *args, **kwargs):
        store = getattr(owner, "store", owner)
        with runtime_guard(store.path):
            return function(owner, *args, **kwargs)

    return wrapper


def known_states():
    return [
        Path(json.loads(file.read_text())["state"])
        for file in sorted((home() / "projects").glob("*/project.json"))
    ]


def require_quiet_state(state):
    state = Path(state)
    if (state / ".pending.json").exists():
        raise RuntimeError("Recover the pending state transaction with status before updating")
    for file in (state / "tasks").glob("*.json"):
        if json.loads(file.read_text()).get("status") == "running":
            raise RuntimeError(
                f"Unresolved running work in {state}; let the driver finish, or stop and reconcile it before updating"
            )
    for file in (state / "attempt-records").glob("*.json"):
        attempt = json.loads(file.read_text())
        pid = attempt.get("pid")
        if attempt.get("status") == "running" and isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise RuntimeError(
                f"A recorded worker process may still be active ({pid}); verify it has stopped before updating"
            )
