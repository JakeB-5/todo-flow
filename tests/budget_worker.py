"""Local adapter for request-budget execution and recovery tests."""

import json
import sys
from pathlib import Path

context = json.load(sys.stdin)
assert Path.cwd().resolve() == Path(context["workspace"])
assert "def add" in Path("calc.py").read_text()
mode = sys.argv[1]
if mode == "fail":
    print("Deliberate local worker failure", file=sys.stderr)
    sys.exit(1)
if mode == "question" and context["task"]["purpose"].startswith("Read the goal"):
    result = {"summary": "Input required", "question": "Which implementation?"}
else:
    result = {
        "summary": "Plan another bounded attempt",
        "next": [{"kind": "work", "purpose": "Repeat the local plan"}],
    }
json.dump(result, sys.stdout)
