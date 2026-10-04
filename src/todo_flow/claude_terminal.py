"""Terminal-side observer for a real interactive, read-only Claude process."""

import hashlib
import json
import os
from pathlib import Path
import time

from .claude_transcript import ClaudeTranscript
from .maintenance import write_json
from .native_viewer import publish


class ClaudeTerminal:
    def __init__(self, folder, spec):
        if spec.get("version") != 1 or spec.get("provider") != "claude":
            raise ValueError("Unsupported interactive worker protocol")
        if not all(os.isatty(fd) for fd in (0, 1, 2)):
            raise ValueError("Claude interactive worker requires an actual terminal")
        if os.environ.get("ORCA_WORKTREE_ID") != spec["worktree"]:
            raise ValueError("Claude terminal is not in the owned Orca worktree")
        projects = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
        if projects.absolute() != Path(spec["projects"]):
            raise ValueError("Claude terminal account home differs from its launch context")
        self.folder = folder
        self.spec = spec
        self.reader = ClaudeTranscript(spec)
        if self.reader.find() is not None:
            raise FileExistsError("Claude session already exists; do not replay the prompt")
        self.started = time.monotonic()
        self.result = None
        self.record = {
            "version": 1,
            **{key: spec[key] for key in ("session", "worktree", "head", "task")},
            "status": "starting",
        }
        self.save()

    def save(self):
        write_json(self.folder / "claude-session.json", self.record)

    def sidebar(self, state):
        try:
            observation = (
                publish(
                    {
                        **self.spec,
                        "transcript": str(self.reader.path),
                        "model": self.record.get("model"),
                    },
                    state,
                    dict(os.environ),
                )
                if self.spec.get("hook")
                else None
            )
            self.record["sidebar"] = {
                "status": "confirmed" if observation else "unconfirmed",
                "observation": observation,
            }
        except Exception as error:
            self.record["sidebar"] = {"status": "unconfirmed", "error": str(error)}

    def poll(self):
        self.result = self.reader.poll()
        if self.reader.turn is not None and self.record["status"] == "starting":
            self.record.update(
                status="turn-accepted", turn=self.reader.turn, transcript=str(self.reader.path)
            )
            self.sidebar("working")
            self.save()
        if self.result is not None:
            self.record.update(
                status="proposal-received",
                **{key: self.result[key] for key in ("turn", "message", "completion", "model")},
            )
            self.save()
            return True
        if self.reader.turn is None and time.monotonic() - self.started > 60:
            raise RuntimeError(
                "Claude did not record the initial prompt; inspect its terminal for startup, "
                "trust or authentication requirements. The prompt must not be resent."
            )
        return False

    def finish(self, success):
        if success and self.result is not None:
            transcript = "".join(json.dumps(row) + "\n" for row in self.reader.rows)
            evidence = self.folder / "claude-transcript.jsonl"
            evidence.write_text(transcript)
            self.record.update(
                status="complete",
                evidence={
                    "path": str(evidence),
                    "sha256": hashlib.sha256(transcript.encode()).hexdigest(),
                },
            )
            write_json(
                self.folder / "output.json",
                {
                    "type": "result",
                    "session_id": self.spec["session"],
                    "is_error": False,
                    "structured_output": self.result["proposal"],
                },
            )
        else:
            self.record["status"] = "stopped"
        if self.reader.turn is not None:
            self.sidebar("stopped")
        self.save()
