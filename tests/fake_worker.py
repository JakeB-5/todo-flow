"""Deterministic test adapter. Production smoke uses fresh Claude sessions instead."""

import json
import sys
from pathlib import Path

ctx = json.load(sys.stdin)
assert ctx["worker_protocol"] == 2
assert Path.cwd().resolve() == Path(ctx["workspace"])
assert "files" not in ctx
ctx["document"] = json.loads(Path(ctx["paths"]["document"]).read_text())
if "triage_context" in ctx["paths"]:
    ctx["triage_context"] = json.loads(Path(ctx["paths"]["triage_context"]).read_text())
# Exercise real reads in the assigned implementation/review/triage checkout.
assert "def add" in Path("calc.py").read_text()
kind = ctx["task"]["kind"]
if kind == "assess":
    result = {
        "summary": "Implement goal",
        "next": [{"kind": "work", "purpose": "Implement addition"}],
    }
elif kind == "work":
    result = {
        "summary": "Implemented and tested addition",
        "changes": [
            {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"},
            {
                "path": "test_calc.py",
                "content": "import unittest\nfrom calc import add\nclass Test(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
            },
        ],
        "verify": True,
        "publish": True,
        "next": [{"kind": "review", "purpose": "Independent review"}],
    }
    if ctx["task"]["purpose"].startswith("Repair original obligations"):
        result["changes"][0]["content"] = (
            "def add(a, b):\n    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):\n        raise TypeError('Numeric operands required')\n    return a + b\n"
        )
        result["changes"][1]["content"] += (
            "    def test_reject_text(self):\n        with self.assertRaises(TypeError):\n            add('2', '3')\n"
        )
elif kind == "review":
    result = {
        "summary": "Independent check: implementation and test satisfy addition",
        "verdict": "met",
        "conditions": [
            {
                "id": c["id"],
                "verdict": "met",
                "evidence": "calc.py and test_calc.py, verification passed",
            }
            for c in ctx["document"]["conditions"]
        ],
        "next": [{"kind": "land", "purpose": "Land verified result"}],
    }
elif kind == "triage":
    result = {
        "summary": "Landed code has no unresolved observations",
        "triage": [
            {
                "source": s["id"],
                "action": "resolved",
                "observation": s.get("observation", "Observed item"),
                "evidence": "Verified landed calc.py and test_calc.py",
                "reason": "The landed result satisfies this observation",
                "scope": "in-scope",
                "confirmed": True,
            }
            for s in ctx["triage_context"]["sources"]
        ],
    }
else:
    result = {"summary": "Unknown test action", "question": kind}
json.dump(result, sys.stdout)
