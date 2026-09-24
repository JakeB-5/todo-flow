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

        def stop(signum, frame):
            raise KeyboardInterrupt

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, stop)

        def relay(stream, path):
            with path.open("wb") as output:
                for chunk in iter(stream.readline, b""):
                    output.write(chunk)
                    output.flush()
                    try:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                    except (BrokenPipeError, OSError):
                        pass

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
            if cancelled.exists():
                raise KeyboardInterrupt
            code = proc.wait()
        except KeyboardInterrupt:
            code = 130
        except Exception as error:
            (folder / "stderr.log").write_text(str(error))
        finally:
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            for thread in readers:
                thread.join()
            save(
                receipt,
                {**state, "status": "exited", "returncode": code, "finished_at": time.time()},
            )
            print(f"\nTODO Flow worker exited: {code}", flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
