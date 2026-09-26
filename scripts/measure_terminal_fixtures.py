"""Measure real local workers behind synthetic terminal CLI boundaries.

Invoke this same tool in separate processes for each clean source revision.
No Orca/tmux executable or external model is invoked. Raw bridge output, worker
receipts, CLI responses and chronological inventories remain in --output.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

BASELINE = "beaa96603e2958ef1cd8a76d453b2cc8e38a1491"
PROGRAM = """import json, os, pathlib, sys, time
context = json.load(sys.stdin)
settings = json.loads(pathlib.Path(context['paths']['fixture']).read_text())
folder = pathlib.Path(context['paths']['fixture']).parent

def save(name, value):
    destination = folder / name
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(destination)

save('worker-start.json', {'pid': os.getpid(), 'at': time.time()})
print('fixture worker ' + context['task']['id'], file=sys.stderr, flush=True)
if settings['hold']:
    deadline = time.monotonic() + settings['barrier_timeout']
    while not pathlib.Path(settings['release']).exists():
        if time.monotonic() >= deadline:
            save('worker-barrier-timeout.json', {'at': time.time()})
            raise TimeoutError('measurement barrier timed out')
        time.sleep(0.01)
time.sleep(0.15)
save('worker-finish.json', {'pid': os.getpid(), 'at': time.time()})
print(json.dumps({'summary': context['task']['id']}), flush=True)
"""


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Fixture:
    def __init__(self, source, output, backend):
        self.source = Path(source).resolve()
        self.output = Path(output).resolve()
        self.backend = backend
        self.state = self.output / "state"
        self.state.mkdir()
        self.release = self.output / "release-barrier"
        self.lock = threading.RLock()
        self.rows = {}
        self.bridges = {}
        self.attempts = {}
        self.executions = []
        self.samples = []
        self.futures = []
        self.errors = []
        self.closed = 0
        self.admission = "not-applicable"
        self.complete = False
        self.stop_monitor = threading.Event()
        self.monitor = None
        self.real_which = shutil.which
        self.argv = [sys.executable, "-c", PROGRAM]
        self.socket_directory = tempfile.TemporaryDirectory(prefix="tf-", dir="/tmp")
        self.socket_path = str(Path(self.socket_directory.name) / "s")
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.socket_path)
        self.config = {
            "repo": str(self.source),
            "worker": {"type": "command", "argv": self.argv},
            "worker_protocol": 2,
            "worker_launcher": "terminal" if backend == "custom" else backend,
            "terminal_command": ["fixture-terminal", "{command}"],
            "worker_timeout": 40,
            "terminal_concurrency": 2,
            "terminal_idle_limit": 1,
        }

    def journal(self, name, event):
        with self.lock:
            with (self.output / name).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": time.time(), **event}) + "\n")
                stream.flush()

    def which(self, command, *args, **kwargs):
        if command in ("fixture-orca", "fixture-terminal", "tmux"):
            return command
        return self.real_which(command, *args, **kwargs)

    def refresh(self):
        with self.lock:
            for handle, bridge in self.bridges.items():
                code = bridge["process"].poll()
                if code is None or bridge["observed"]:
                    continue
                bridge["stream"].close()
                bridge["observed"] = True
                log = bridge["log"]
                receipt = {
                    "pid": bridge["process"].pid,
                    "returncode": code,
                    "observed_at": time.time(),
                    "stdout": str(log.relative_to(self.output)),
                    "stdout_sha256": digest(log),
                }
                save(log.parent / "fixture-bridge-exit.json", receipt)
                row = self.rows.get(handle)
                if row is not None:
                    if self.backend == "tmux":
                        del self.rows[handle]
                    else:
                        row.update(
                            preview=log.read_text(errors="replace"),
                            lastOutputAt=log.stat().st_mtime * 1000,
                            connected=not bridge["exec"],
                            writable=not bridge["exec"],
                        )
                self.journal("inventory-events.jsonl", {"bridge_exit": handle, **receipt})

    def sample(self, label):
        with self.lock:
            self.refresh()
            pids = []
            for folder in self.attempts:
                start = folder / "worker-start.json"
                if start.exists():
                    pid = json.loads(start.read_text())["pid"]
                    if alive(pid):
                        pids.append(pid)
            row = {
                "sequence": len(self.samples),
                "at": time.time(),
                "label": label,
                "owned_tabs": len(self.rows),
                "active_tabs": sum(row["connected"] for row in self.rows.values()),
                "active_worker_pids": sorted(pids),
                "foreign_tabs": 0 if self.backend == "headless" else 2,
                "inventory": [dict(row) for row in self.rows.values()],
            }
            self.samples.append(row)
            self.journal("samples.jsonl", row)
            return row

    def register(self, folder, task):
        with self.lock:
            if folder in self.attempts:
                raise ValueError("An attempt must never be reused")
            self.attempts[folder] = task

    def create(self, command, title=None):
        with self.lock:
            words = shlex.split(command)
            is_exec = bool(words and words[0] == "exec")
            if is_exec:
                words = words[1:]
            if (
                len(words) != 3
                or Path(words[0]).resolve() != Path(sys.executable).resolve()
                or Path(words[1]).resolve() != self.source / "src/todo_flow/terminal_worker.py"
            ):
                raise ValueError("Unexpected bridge command")
            spec_path = Path(words[2]).resolve()
            folder = spec_path.parent
            if spec_path.name != "terminal-spec.json" or folder not in self.attempts:
                raise ValueError("Bridge spec is not a registered attempt")
            if any(bridge["folder"] == folder for bridge in self.bridges.values()):
                raise ValueError("Duplicate physical launch")
            spec = json.loads(spec_path.read_text())
            argv = spec.get("argv", [])
            if (
                len(argv) != 3
                or Path(argv[0]).resolve() != Path(sys.executable).resolve()
                or argv[1:] != self.argv[1:]
                or Path(spec["cwd"]).resolve() != self.source
                or (title is not None and title != spec["title"])
            ):
                raise ValueError("Bridge spec does not identify the local measurement worker")
            number = len(self.bridges) + 1
            handle = f"@{number}" if self.backend == "tmux" else f"term-{number}"
            row = {
                "handle": handle,
                "ptyId": f"pty-{number}",
                "tabId": f"tab-{number}",
                "incarnationId": f"incarnation-{number}",
                "executionHostId": "local",
                "worktreeId": "fixture",
                "title": spec["title"],
                "connected": True,
                "writable": True,
                "orphaned": False,
                "preview": "",
                "lastOutputAt": time.time() * 1000,
            }
            log = folder / "fixture-bridge.stdout"
            stream = log.open("wb")
            try:
                process = subprocess.Popen(
                    words,
                    cwd=self.source,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except BaseException:
                stream.close()
                raise
            self.rows[handle] = row
            self.bridges[handle] = {
                "process": process,
                "stream": stream,
                "log": log,
                "folder": folder,
                "exec": is_exec,
                "observed": False,
            }
            self.journal(
                "inventory-events.jsonl",
                {"create": handle, "command": command, "bridge_pid": process.pid},
            )
            self.sample("create")
            return handle

    def dispatch(self, argv):
        if argv == ["fixture-orca", "status", "--json"]:
            return {"app": {"running": True}, "runtime": {"reachable": True}}
        if argv == [
            "fixture-orca",
            "worktree",
            "show",
            "--worktree",
            "path:" + str(self.source),
            "--json",
        ]:
            return {"worktree": {"id": "fixture", "hostId": "local"}}
        if argv[:3] == ["fixture-orca", "terminal", "create"]:
            if (
                len(argv) != 10
                or argv[3:6] != ["--worktree", "id:fixture", "--title"]
                or argv[7] != "--command"
                or argv[9] != "--json"
            ):
                raise ValueError("Unsupported Orca create arguments")
            handle = self.create(argv[8], argv[6])
            return {"terminal": dict(self.rows[handle])}
        if argv == [
            "fixture-orca",
            "terminal",
            "list",
            "--worktree",
            "id:fixture",
            "--limit",
            "100000",
            "--json",
        ]:
            foreign = [
                {
                    "handle": name,
                    "ptyId": name,
                    "tabId": name,
                    "executionHostId": "local",
                    "worktreeId": "fixture",
                }
                for name in ("user", "dashboard")
            ]
            rows = [*foreign, *(dict(row) for row in self.rows.values())]
            return {
                "terminals": rows,
                "totalCount": len(rows),
                "truncated": False,
                "hostScope": {"hostIds": ["local"], "omittedHostIds": []},
            }
        if argv[:2] == ["fixture-orca", "terminal"] and len(argv) >= 6:
            action = argv[2]
            handle = argv[4]
            tail = ["--terminal", handle]
            if action == "wait":
                tail += ["--for", "exit", "--timeout-ms", "1000"]
            if action not in ("wait", "show", "close") or argv[3:] != [*tail, "--json"]:
                raise ValueError("Unsupported Orca terminal arguments")
            bridge = self.bridges[handle]
            row = self.rows[handle]
            if action == "wait":
                try:
                    bridge["process"].wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                self.refresh()
                exited = bridge["observed"] and bridge["exec"]
                return {
                    "wait": {
                        "handle": handle,
                        "condition": "exit",
                        "satisfied": exited,
                        "status": "exited" if exited else "running",
                        "exitCode": bridge["process"].returncode if exited else None,
                    }
                }
            if action == "show":
                return {"terminal": dict(row)}
            if row["connected"] or not bridge["observed"] or not bridge["exec"]:
                raise ValueError("Fixture refuses close of an active shell or bridge")
            del self.rows[handle]
            self.closed += 1
            return {"close": {"handle": handle, "ptyKilled": False}}
        if argv[:3] == ["tmux", "-S", self.socket_path]:
            if argv[3:] == ["list-windows", "-a", "-F", "#{window_id}"]:
                return "\n".join(["@0", "@999999", *self.rows]) + "\n"
            if (
                len(argv) == 13
                and argv[3:9] == ["new-window", "-d", "-P", "-F", "#{window_id}", "-n"]
                and argv[10:12] == ["-c", str(self.source)]
            ):
                return self.create(argv[12], argv[9]) + "\n"
        if len(argv) == 2 and argv[0] == "fixture-terminal":
            return self.create(argv[1]) + "\n"
        raise ValueError("Unsupported fixture CLI: " + repr(argv))

    def cli(self, argv, **kwargs):
        with self.lock:
            argv = list(argv)
            try:
                expected = {"orca": "fixture-orca", "tmux": "tmux", "custom": "fixture-terminal"}
                if not argv or argv[0] != expected.get(self.backend):
                    raise ValueError("CLI does not belong to selected backend")
                self.refresh()
                value = self.dispatch(argv)
                output = (
                    value
                    if isinstance(value, str)
                    else json.dumps(
                        {"ok": True, "result": value, "_meta": {"runtimeId": "fixture-runtime"}}
                    )
                )
                self.journal(
                    "cli.jsonl",
                    {"argv": argv, "returncode": 0, "stdout": output, "stderr": ""},
                )
                self.sample("cli-return")
                return subprocess.CompletedProcess(argv, 0, output, "")
            except BaseException as error:
                self.journal("cli.jsonl", {"argv": argv, "error": repr(error)})
                raise

    @contextmanager
    def activate(self):
        with (
            patch.dict(
                os.environ,
                {
                    "ORCA_CLI_COMMAND": "fixture-orca",
                    "TMUX": self.socket_path + ",1,0",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            ),
            patch.object(shutil, "which", side_effect=self.which),
            patch.object(subprocess, "run", side_effect=self.cli),
        ):
            yield

    def observe(self):
        try:
            while not self.stop_monitor.wait(0.02):
                self.sample("poll")
        except BaseException as error:
            with self.lock:
                self.errors.append("monitor: " + repr(error))

    def execute(self, run_worker, number, retry=0, hold=False):
        name = f"request-{number:03d}-try-{retry}"
        task = {
            "id": name,
            "track": f"fixture-{number:03d}",
            "attempt": name,
            "generation": 1,
            "kind": "work" if number % 2 == 0 else "review",
        }
        folder = self.state / "attempts" / name
        self.register(folder, task)
        record = {"request": number, "task": task, "started_at": time.time()}
        try:
            result = run_worker(
                self.config,
                {
                    "workspace": str(self.source),
                    "task": task,
                    "fixture": {
                        "release": str(self.release),
                        "hold": hold,
                        "barrier_timeout": 20,
                    },
                },
                task,
                self.state,
                lambda _: self.sample("heartbeat"),
            )
            returned = time.time()
            if result.get("summary") != name:
                raise ValueError("Worker output attribution failed")
            with self.lock:
                bridges = [b for b in self.bridges.values() if b["folder"] == folder]
            for bridge in bridges:
                bridge["process"].wait(timeout=5)
            self.sample("run-worker-return")
            start = json.loads((folder / "worker-start.json").read_text())
            finish = json.loads((folder / "worker-finish.json").read_text())
            if start["pid"] != finish["pid"] or alive(start["pid"]):
                raise ValueError("Worker process exit was not observed after return")
            names = ["input.json", "output.json", "stderr.log", "launch.json"]
            names += ["worker-start.json", "worker-finish.json"]
            if self.backend != "headless":
                names += [
                    "terminal-spec.json",
                    "terminal-process.json",
                    "fixture-bridge.stdout",
                    "fixture-bridge-exit.json",
                ]
                receipt = json.loads((folder / "terminal-process.json").read_text())
                if receipt.get("status") != "exited" or receipt.get("returncode") != 0:
                    raise ValueError("Missing successful terminal exit receipt")
                if len(bridges) != 1 or bridges[0]["process"].returncode != 0:
                    raise ValueError("Bridge exit is not successful")
                log = (folder / "fixture-bridge.stdout").read_text()
                if not log.rstrip().endswith("TODO Flow worker exited: 0"):
                    raise ValueError("Actual bridge final output is missing")
            record.update(
                outcome="completed",
                returned_at=returned,
                exit_observed_at=time.time(),
                worker_pid=start["pid"],
                worker_alive_after_return=False,
                evidence={name: digest(folder / name) for name in names},
            )
        except Exception as error:
            if (
                type(error).__name__ == "TerminalCapacityError"
                and type(error).__module__ == "todo_flow.terminal_slots"
            ):
                with self.lock:
                    dispatched = any(b["folder"] == folder for b in self.bridges.values())
                if dispatched or (folder / "terminal-spec.json").exists():
                    raise ValueError("Capacity rejection occurred after dispatch") from error
                record.update(outcome="blocked", error=str(error))
            else:
                record.update(outcome="failed", error=repr(error))
                raise
        finally:
            with self.lock:
                self.executions.append(record)
                self.journal("executions.jsonl", record)
        return record["outcome"]

    def wait_pair(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.sample("barrier-wait")
            pids = row["active_worker_pids"]
            if len(pids) == 2 and len(set(pids)) == 2:
                if any(future.done() for future in self.futures[:2]):
                    raise ValueError("Worker returned while barrier should be held")
                if not all(alive(pid) for pid in pids):
                    continue
                save(self.output / "barrier-pair.json", row)
                return
            time.sleep(0.02)
        raise TimeoutError("Two live worker processes did not reach the barrier")

    def measure(self, run_worker, candidate, schedule, count=50, barrier_wait=10):
        if count < 3:
            raise ValueError("At least three requests are needed")
        pool = ThreadPoolExecutor(max_workers=3)
        self.monitor = threading.Thread(target=self.observe, daemon=True)
        self.monitor.start()

        def submit(number, retry=0, hold=False):
            future = pool.submit(self.execute, run_worker, number, retry, hold)
            self.futures.append(future)
            return future

        try:
            with self.activate():
                try:
                    remaining = list(range(count))
                    if schedule == "saturation":
                        first = submit(0, hold=True)
                        second = submit(1, hold=True)
                        self.wait_pair(barrier_wait)
                        remaining = list(range(2, count))
                        if self.backend != "headless":
                            before = len(self.bridges)
                            third = submit(2)
                            outcome = third.result(timeout=5)
                            self.admission = outcome
                            if candidate and (outcome != "blocked" or len(self.bridges) != before):
                                raise ValueError(
                                    "Third visible request was not blocked before create"
                                )
                            if not candidate and outcome != "completed":
                                raise ValueError("Baseline unexpectedly rejected admission")
                            self.sample("third-request-returned-with-barrier-held")
                        self.release.touch()
                        if first.result(timeout=60) != "completed":
                            raise ValueError("First worker did not complete")
                        if second.result(timeout=60) != "completed":
                            raise ValueError("Second worker did not complete")
                        if self.backend != "headless":
                            remaining.remove(2)
                            if self.admission == "blocked" and self.backend != "custom":
                                if submit(2, retry=1).result(timeout=60) != "completed":
                                    raise ValueError("Released capacity did not admit the retry")
                    for number in remaining:
                        submit(number).result(timeout=60)
                finally:
                    self.release.touch()
                    for future in self.futures:
                        try:
                            future.result(timeout=65)
                        except Exception as error:
                            self.errors.append("future: " + repr(error))
                    pool.shutdown(wait=False, cancel_futures=True)
                    self.reap_bridges()
            self.stop_monitor.set()
            self.monitor.join(timeout=5)
            self.sample("final")
            if self.monitor.is_alive() or self.errors:
                raise ValueError("Incomplete collection: " + repr(self.errors))
            self.check_evidence()
            report = self.report()
            expected_completed = 2 if candidate and self.backend == "custom" else count
            if report["completed"] != expected_completed:
                raise ValueError("Unexpected completed request count")
            if candidate:
                if report["max_owned_tabs"] > 3 or report["max_active_worker_processes"] > 2:
                    raise ValueError("Candidate resource bound exceeded")
                if self.backend == "custom":
                    if report["blocked_requests"] != count - 2 or report["preserved"] != 2:
                        raise ValueError(
                            "Custom backend did not retain two and block the remainder"
                        )
                elif report["preserved"] != 0:
                    raise ValueError("Candidate retained an owned tab")
            if self.backend == "headless" and report["created"] != 0:
                raise ValueError("Headless unexpectedly created a terminal")
            if schedule == "saturation" and report["max_active_worker_processes"] < 2:
                raise ValueError("No evidence of two simultaneous worker processes")
            self.complete = True
        finally:
            self.release.touch()
            self.stop_monitor.set()
            if self.monitor is not None:
                self.monitor.join(timeout=5)
            pool.shutdown(wait=False, cancel_futures=True)

    def reap_bridges(self):
        with self.lock:
            bridges = list(self.bridges.values())
        for bridge in bridges:
            process = bridge["process"]
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.errors.append("Bridge required cancellation: " + str(process.pid))
                (bridge["folder"] / "terminal-cancelled").touch()
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.errors.append("Bridge did not stop: " + str(process.pid))
                    continue
        self.refresh()

    def check_evidence(self):
        for record in self.executions:
            if record["outcome"] != "completed":
                continue
            folder = self.state / "attempts" / record["task"]["attempt"]
            for name, expected in record["evidence"].items():
                if digest(folder / name) != expected:
                    raise ValueError("Execution evidence changed: " + str(folder / name))

    def report(self):
        with self.lock:
            completed = {r["request"] for r in self.executions if r["outcome"] == "completed"}
            blocked = {r["request"] for r in self.executions if r["outcome"] == "blocked"}
            return {
                "measurement_complete": self.complete and not self.errors,
                "completed": len(completed),
                "blocked_requests": len(blocked - completed),
                "blocked_calls": sum(r["outcome"] == "blocked" for r in self.executions),
                "created": len(self.bridges),
                "reused": 0,
                "reclaimed": len(self.bridges) - len(self.rows),
                "preserved": len(self.rows),
                "close_calls": self.closed,
                "terminal_admission": self.admission,
                "max_owned_tabs": max((r["owned_tabs"] for r in self.samples), default=0),
                "max_active_tabs": max((r["active_tabs"] for r in self.samples), default=0),
                "max_active_worker_processes": max(
                    (len(r["active_worker_pids"]) for r in self.samples), default=0
                ),
                "errors": list(self.errors),
            }

    def close(self):
        self.release.touch()
        self.stop_monitor.set()
        if self.monitor is not None:
            self.monitor.join(timeout=5)
        self.reap_bridges()
        self.server.close()
        self.socket_directory.cleanup()


def source_identity(source):
    def git(*argv):
        return subprocess.run(
            ["git", "-C", str(source), *argv],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "tracked_changes": git("status", "--porcelain", "--untracked-files=no"),
    }


def import_paths(source):
    paths = {}
    root = source / "src"
    for name, module in list(sys.modules.items()):
        if name == "todo_flow" or name.startswith("todo_flow."):
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Imported module is outside selected source: " + str(path))
            paths[name] = str(path)
    for name in ("todo_flow.worker", "todo_flow.launchers"):
        expected = root / (name.replace(".", "/") + ".py")
        if paths.get(name) != str(expected):
            raise ValueError("Selected source module was not imported: " + name)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("orca", "tmux", "custom", "headless"), required=True)
    parser.add_argument("--schedule", choices=("sequential", "saturation"), required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Evidence directory must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    fixture = None
    failure = None
    before = None
    method = {
        "fixture": "synthetic-cli-real-local-worker-and-bridge",
        "source": str(source),
        "expected_head": args.expected_head,
        "baseline_reference": BASELINE,
        "backend": args.backend,
        "schedule": args.schedule,
        "requests": 50,
        "python": sys.executable,
        "tool_sha256": digest(Path(__file__)),
        "limitations": [
            "Terminal CLI responses are synthetic; this is not a live terminal measurement",
            "Active worker processes are sampled; the barrier separately proves overlap",
            "Headless has no terminal admission limit",
            "Baseline is measured without candidate capacity assertions",
        ],
    }
    save(output / "method.json", method)
    try:
        before = source_identity(source)
        method["before"] = before
        if before["head"] != args.expected_head or before["tracked_changes"]:
            raise ValueError("Source must have exact expected HEAD and no tracked changes")
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(source / "src"))
        worker = importlib.import_module("todo_flow.worker")
        method["imports_before"] = import_paths(source)
        fixture = Fixture(source, output, args.backend)
        method["config"] = fixture.config
        save(output / "method.json", method)
        fixture.measure(worker.run_worker, before["head"] != BASELINE, args.schedule)
    except BaseException as error:
        failure = repr(error)
    finally:
        if fixture is not None:
            try:
                fixture.close()
                fixture.check_evidence()
            except BaseException as error:
                failure = failure or repr(error)
        try:
            after = source_identity(source)
            method["after"] = after
            method["imports_after"] = import_paths(source)
            if after != before or after["tracked_changes"]:
                raise ValueError("Source identity or tracked content changed during measurement")
        except BaseException as error:
            failure = failure or repr(error)
        save(output / "method.json", method)
        report = fixture.report() if fixture is not None else {"measurement_complete": False}
        report.update(head=args.expected_head, backend=args.backend, schedule=args.schedule)
        report["error"] = failure
        report["measurement_complete"] = report["measurement_complete"] and failure is None
        save(output / "report.json", report)
        manifest = {}
        for path in sorted(output.rglob("*")):
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                continue
            manifest[str(path.relative_to(output))] = digest(path)
        save(output / "manifest.json", manifest)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["measurement_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
