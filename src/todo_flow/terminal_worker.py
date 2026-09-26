"""Standalone terminal bridge: show worker output and keep durable exit evidence."""

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

if __package__:
    from .verification import VerificationCleanupError, stop_group
else:
    # Launchers invoke this file directly, including outside an installed package.
    from verification import VerificationCleanupError, stop_group


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def main(spec_path):
    folder = Path(spec_path).parent
    spec = json.loads(Path(spec_path).read_text())
    receipt = folder / "terminal-process.json"
    cancelled = folder / "terminal-cancelled"
    with (folder / "terminal-run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        # A terminal launch may be delivered twice. Never repeat an attempt.
        if receipt.exists() or cancelled.exists():
            return 1
        state = {"runner_pid": os.getpid(), "status": "starting"}
        save(receipt, state)
        proc = None
        readers = []
        reader_errors = []

        def stop(signum, frame):
            raise KeyboardInterrupt

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, stop)

        def relay(stream, path):
            try:
                with path.open("wb") as output:
                    for chunk in iter(stream.readline, b""):
                        output.write(chunk)
                        output.flush()
                        try:
                            sys.stdout.buffer.write(chunk)
                            sys.stdout.buffer.flush()
                        except (BrokenPipeError, OSError):
                            pass
            except Exception as error:
                reader_errors.append(str(error))

        code = 1
        try:
            env = dict(os.environ)
            env.pop("CLAUDECODE", None)
            print(f"TODO Flow worker: {spec['title']}\nWorkspace: {spec['cwd']}", flush=True)
            with (folder / "input.json").open("rb") as inp:
                proc = subprocess.Popen(
                    spec["argv"],
                    cwd=spec["cwd"],
                    env=env,
                    stdin=inp,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            state.update(pid=proc.pid, status="running")
            save(receipt, state)
            for stream, name in ((proc.stdout, "output.json"), (proc.stderr, "stderr.log")):
                thread = threading.Thread(target=relay, args=(stream, folder / name), daemon=True)
                thread.start()
                readers.append(thread)
            while True:
                if cancelled.exists():
                    raise KeyboardInterrupt
                try:
                    code = proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except KeyboardInterrupt:
            code = 130
        except Exception as error:
            state["error"] = str(error)
        finally:
            state.update(status="cleaning", worker_returncode=code)
            save(receipt, state)
            try:
                if proc is not None:
                    # Parent exit says nothing about inherited pipes or descendants.
                    # Readers own the pipes, so communicate must not consume them.
                    stop_group(proc, collect_output=False)
                deadline = time.monotonic() + 5
                for thread in readers:
                    thread.join(timeout=max(0, deadline - time.monotonic()))
                if any(thread.is_alive() for thread in readers):
                    raise VerificationCleanupError("Terminal output readers did not stop")
                if reader_errors:
                    raise VerificationCleanupError(
                        "Cannot preserve terminal output: " + "; ".join(reader_errors)
                    )
            except BaseException as error:
                save(
                    receipt,
                    {
                        **state,
                        "status": "cleanup_failed",
                        "cleanup_error": f"{type(error).__name__}: {error}",
                        "cleanup_checked_at": time.time(),
                    },
                )
                raise
            save(
                receipt,
                {
                    **state,
                    "status": "exited",
                    "returncode": code,
                    "cleanup_confirmed": True,
                    "finished_at": time.time(),
                },
            )
            print(f"\nTODO Flow worker exited: {code}", flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
