"""Host-only, local-command Orca measurement; never invokes a model.

Run this same script against separate clean source checkouts using --source and
--expected-head. The pre-change reference is
beaa96603e2958ef1cd8a76d453b2cc8e38a1491 (the authored observation revision).
Use a fresh --output for every run and an existing local Orca --workspace.
Example: python scripts/measure_orca_workers.py --source /candidate
--expected-head FULL_SHA --workspace /orca/worktree --output /evidence/candidate

This is a sequential 50-run experiment with C=2/I=1 configuration, not a
concurrent saturation experiment. Baseline shells may remain open. This script
never adds cleanup, retries, reuse, fallback, or capacity overrides. Only the
selected source revision's run_worker and retirement adapter can close tabs.
An error stops the experiment and preserves all evidence. Existing tabs are
excluded by recorded physical identity, never by a TODO title prefix.
Raw inventory can contain local project information; keep output outside Git.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch


BASELINE = "beaa96603e2958ef1cd8a76d453b2cc8e38a1491"
KEYS = ("executionHostId", "worktreeId", "tabId", "ptyId", "handle")
PROGRAM = (
    "import json,sys,time; data=json.load(sys.stdin); "
    "task=data['task']; print('local synthetic worker '+task['id'], file=sys.stderr); "
    "time.sleep(0.2); print(json.dumps({'summary':task['id']}))"
)


def identity(row):
    values = tuple(row.get(key) for key in KEYS)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("Incomplete physical identity")
    return values


def inventory(payload, runtime, worktree):
    if payload.get("ok") is not True or payload.get("_meta", {}).get("runtimeId") != runtime:
        raise ValueError("Failed inventory or changed runtime")
    result = payload["result"]
    rows = result.get("terminals")
    scope = result.get("hostScope", {})
    if (
        not isinstance(rows, list)
        or result.get("truncated") is not False
        or type(result.get("totalCount")) is not int
        or result["totalCount"] != len(rows)
        or scope.get("hostIds") != ["local"]
        or scope.get("omittedHostIds") != []
    ):
        raise ValueError("Incomplete local inventory")
    values = [identity(row) for row in rows]
    if len(set(values)) != len(values) or any(
        value[0] != "local" or value[1] != worktree for value in values
    ):
        raise ValueError("Conflicting inventory identity")
    return set(values)


def counts(created, present):
    """Absence is usable only after inventory() validated the same runtime."""
    owned = set(created)
    return {
        "created": len(created),
        "reused": len(created) - len(owned),
        "reclaimed": len(owned - present),
        "preserved": len(owned & present),
        "foreign_present": len(present - owned),
    }


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    workspace = args.workspace.resolve(strict=True)
    output = args.output.resolve()
    real_run = subprocess.run

    def git(*argv):
        return real_run(
            ["git", "-C", str(source), *argv],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    head = git("rev-parse", "HEAD")
    if head != args.expected_head or git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Source must have the exact expected HEAD and no tracked changes")
    if output == source or source in output.parents:
        raise ValueError("Evidence must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    state = output / "state"
    state.mkdir()
    sys.path.insert(0, str(source / "src"))
    worker = importlib.import_module("todo_flow.worker")
    launchers = importlib.import_module("todo_flow.launchers")
    if Path(worker.__file__).resolve() != source / "src/todo_flow/worker.py":
        raise ValueError("Imported worker does not belong to the selected source")
    config = {
        "repo": str(workspace),
        "worker": {"type": "command", "argv": [sys.executable, "-c", PROGRAM]},
        "worker_protocol": 2,
        "worker_launcher": "orca",
        "worker_timeout": 30,
        "terminal_concurrency": 2,
        "terminal_idle_limit": 1,
    }
    save(
        output / "method.json",
        {
            "head": head,
            "tree": git("rev-parse", "HEAD^{tree}"),
            "baseline_reference": BASELINE,
            "source": str(source),
            "workspace": str(workspace),
            "python": sys.executable,
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "fixture": "real-orca-local-python-sequential",
            "config": config,
            "requests": 50,
            "limitations": [
                "Sequential workload does not measure C=2 saturation",
                "No additional cleanup is performed for old revisions",
                "Physical inventory samples are observations, not a continuous trace",
            ],
        },
    )
    events = output / "cli.jsonl"
    completed = []
    created = []
    samples = []
    current_task = None
    launcher = None
    runtime = None
    failure = None

    def record(event):
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"task": current_task, **event}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def call(argv, **kwargs):
        started = time.time()
        try:
            result = real_run(argv, **kwargs)
        except BaseException as error:
            record(
                {
                    "argv": argv,
                    "started_at": started,
                    "finished_at": time.time(),
                    "error": repr(error),
                    "stdout": str(getattr(error, "stdout", "")),
                    "stderr": str(getattr(error, "stderr", "")),
                }
            )
            raise
        record(
            {
                "argv": argv,
                "started_at": started,
                "finished_at": time.time(),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        return result

    def sample(label):
        response = call(
            [
                launcher["cli"],
                "terminal",
                "list",
                "--worktree",
                launcher["worktree"],
                "--limit",
                "100000",
                "--json",
            ],
            cwd=launcher["repo"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        payload = json.loads(response.stdout)
        present = inventory(payload, runtime, launcher["worktree"].removeprefix("id:"))
        row = {"label": label, "at": time.time(), **counts(created, present)}
        samples.append(row)
        save(output / "samples.json", samples)
        return present

    def observed_run(argv, **kwargs):
        result = call(argv, **kwargs)
        if launcher is not None and list(argv[:3]) == [launcher["cli"], "terminal", "create"]:
            payload = json.loads(result.stdout)
            if payload.get("ok") is not True:
                raise ValueError("Unconfirmed create; stop without retry")
            if payload.get("_meta", {}).get("runtimeId") != runtime:
                raise ValueError("Runtime changed during create; stop without retry")
            created.append(identity(payload["result"]["terminal"]))
            save(output / "created.json", created)
            sample("after-create")
        return result

    try:
        # Delegates every subprocess to the real executable, preserving responses.
        # No launch, process, inventory, ownership or retirement result is mocked.
        with patch.object(subprocess, "run", side_effect=observed_run):
            launcher = launchers.select_launcher(config, str(workspace))
            if launcher["backend"] != "orca":
                raise ValueError("Explicit Orca selection did not select Orca")
            status = call(
                [launcher["cli"], "status", "--json"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            payload = json.loads(status.stdout)
            runtime = payload.get("_meta", {}).get("runtimeId")
            if payload.get("ok") is not True or not runtime:
                raise ValueError("Missing runtime identity")
            sample("before")
            for number in range(50):
                current_task = f"local-{number:03d}"
                task = {
                    "id": current_task,
                    "track": "local-orca-measurement",
                    "attempt": current_task,
                    "generation": 1,
                    "kind": "work" if number % 2 == 0 else "review",
                }
                started = time.time()
                save(output / "current.json", {"task": task, "started_at": started})
                result = worker.run_worker(
                    config,
                    {"workspace": str(workspace), "task": task},
                    task,
                    state,
                    lambda _: None,
                )
                if result["summary"] != current_task:
                    raise ValueError("Worker output is not attributable to this task")
                folder = state / "attempts" / current_task
                for name in (
                    "input.json",
                    "output.json",
                    "stderr.log",
                    "launch.json",
                    "terminal-process.json",
                ):
                    if not (folder / name).is_file():
                        raise ValueError("Missing execution evidence: " + str(folder / name))
                completed.append({"task": task, "started_at": started, "finished_at": time.time()})
                save(output / "executions.json", completed)
                sample("after-return")
    except BaseException as error:
        failure = repr(error)
        raise
    finally:
        # Keep state, launch/exit receipts, adapter journals and raw logs in place.
        hashes = {}
        for path in state.rglob("*"):
            if path.is_file() and not path.is_symlink():
                hashes[str(path.relative_to(output))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        save(output / "manifest.json", hashes)
        ledger_path = state / "terminal-slots.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        save(
            output / "report.json",
            {
                "head": head,
                "fixture": "real-orca-local-python-sequential",
                "requested": 50,
                "completed": len(completed),
                "error": failure,
                "last_complete_inventory": samples[-1] if samples else None,
                "max_owned_observed": max((row["preserved"] for row in samples), default=0),
                "max_owned_ledger": ledger.get("max_owned"),
                "max_active_workers": 1 if completed else None,
                "max_active_method": "Sequential run_worker calls with matching output and exit receipts",
                "created_responses": len(created),
                "measurement_complete": failure is None and len(completed) == 50,
                "evidence_manifest": "manifest.json",
                "cli_receipts": "cli.jsonl",
            },
        )


if __name__ == "__main__":
    main()
