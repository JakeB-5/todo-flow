"""Read one host-selected Claude terminal session, never scrape its screen.

Only a completed first turn may supply a proposal. Session identity, the exact
initial prompt, tool boundaries and the terminal turn-duration record are all
required; an assistant JSON fragment or an idle-looking terminal is insufficient.
Unknown/incomplete transcript formats fail closed rather than launch a replacement.
"""

import json
import os
from pathlib import Path
import stat


MAX_TRANSCRIPT_BYTES = 64_000_000


class ClaudeTranscript:
    def __init__(self, spec):
        self.session = spec["session"]
        self.workspace = Path(spec["workspace"]).resolve()
        self.projects = Path(spec["projects"])
        self.prompt = spec["prompt"]
        self.path = None
        self.identity = None
        self.offset = 0
        self.buffer = b""
        self.turn = None
        self.last = None
        self.pending_tools = set()
        self.finished = None
        self.rows = []

    def find(self):
        candidates = list(self.projects.glob("*/" + self.session + ".jsonl"))
        if len(candidates) > 1:
            raise ValueError("Claude session has multiple transcript paths")
        if not candidates:
            return None
        path = candidates[0]
        if path.is_symlink() or path.parent.is_symlink() or self.projects.is_symlink():
            raise ValueError("Claude transcript path is a symlink")
        return path

    def poll(self):
        path = self.find()
        if path is None:
            if self.path is not None:
                raise ValueError("Claude transcript disappeared")
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_TRANSCRIPT_BYTES
                or info.st_size < self.offset
                or (self.identity is not None and identity != self.identity)
                or (self.path is not None and path != self.path)
            ):
                raise ValueError("Claude transcript changed identity or exceeded its bound")
            self.path, self.identity = path, identity
            stream.seek(self.offset)
            chunk = stream.read(MAX_TRANSCRIPT_BYTES + 1)
            self.offset += len(chunk)
        if self.offset > MAX_TRANSCRIPT_BYTES:
            raise ValueError("Claude transcript exceeded its bound")
        lines = (self.buffer + chunk).split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            if line.strip():
                self.receive(json.loads(line))
        # A partially written row after the marker cannot establish a stable turn.
        return self.finished if not self.buffer else None

    def receive(self, row):
        if not isinstance(row, dict):
            raise ValueError("Invalid Claude transcript row")
        if "sessionId" in row and row["sessionId"] != self.session:
            raise ValueError("Claude transcript session mismatch")
        if "cwd" in row and Path(row["cwd"]).resolve() != self.workspace:
            raise ValueError("Claude transcript workspace mismatch")
        kind = row.get("type")
        if kind not in ("user", "assistant", "system"):
            return
        if row.get("sessionId") != self.session or row.get("isSidechain") is True:
            raise ValueError("Claude message is not from the owned main session")
        if not isinstance(row.get("uuid"), str) or not row["uuid"]:
            raise ValueError("Claude message has no identity")
        self.rows.append(row)
        if kind == "user":
            content = row.get("message", {}).get("content")
            if (
                isinstance(content, list)
                and content
                and all(block.get("type") == "tool_result" for block in content)
            ):
                for block in content:
                    tool = block.get("tool_use_id")
                    if tool not in self.pending_tools:
                        raise ValueError("Claude tool result has no matching invocation")
                    self.pending_tools.remove(tool)
                return
            if self.turn is not None or content != self.prompt or row.get("isMeta"):
                raise ValueError("Claude session received an unexpected prompt")
            self.turn = row["uuid"]
        elif kind == "assistant":
            if self.turn is None or self.finished is not None:
                raise ValueError("Claude response is outside the owned turn")
            if row.get("isApiErrorMessage"):
                raise RuntimeError("Claude API failed: " + str(row.get("error", "unknown")))
            message = row.get("message", {})
            content = message.get("content")
            if not isinstance(content, list):
                raise ValueError("Invalid Claude assistant content")
            for block in content:
                if block.get("type") == "tool_use":
                    if block.get("name") not in ("Read", "Glob", "Grep"):
                        raise ValueError(
                            "Claude session used a tool outside its read-only contract"
                        )
                    tool = block.get("id")
                    if not isinstance(tool, str) or not tool or tool in self.pending_tools:
                        raise ValueError("Invalid Claude tool invocation identity")
                    self.pending_tools.add(tool)
            self.last = row
        elif row.get("subtype") == "turn_duration":
            if (
                self.turn is None
                or self.last is None
                or self.pending_tools
                or self.finished is not None
                or row.get("pendingBackgroundAgentCount", 0)
                or row.get("pendingWorkflowCount", 0)
            ):
                raise ValueError("Claude turn did not complete a single bounded response")
            message = self.last["message"]
            if message.get("stop_reason") not in (None, "end_turn", "stop_sequence"):
                raise ValueError("Claude response ended before a complete proposal")
            content = message["content"]
            if any(block.get("type") not in ("text", "thinking") for block in content):
                raise ValueError("Claude final response is not text")
            text = "".join(block["text"] for block in content if block.get("type") == "text")
            proposal = json.loads(text)
            if not isinstance(proposal, dict):
                raise ValueError("Claude proposal must be an object")
            self.finished = {
                "proposal": proposal,
                "turn": self.turn,
                "message": self.last["uuid"],
                "completion": row["uuid"],
                "model": message.get("model"),
            }
        elif self.turn is not None and row.get("subtype") in (
            "compact_boundary",
            "local_command",
        ):
            raise ValueError("Claude turn history changed; reconciliation is required")
