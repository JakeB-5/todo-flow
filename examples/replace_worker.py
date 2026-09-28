"""Read-only command worker example for one explicitly selected replacement."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("old")
    parser.add_argument("new")
    args = parser.parse_args()
    context = json.load(sys.stdin)
    if context["worker_protocol"] != 2 or context["task"]["kind"] != "work":
        raise ValueError("This example requires a protocol-2 work task")
    original = (Path(context["workspace"]) / args.path).read_bytes()
    original.decode("utf-8", errors="strict")
    result = {
        "summary": "지정된 문자열의 replace-v1 치환을 제안합니다.",
        "changes": [
            {
                "path": args.path,
                "format": "replace-v1",
                "base_head": context["head"],
                "sha256": hashlib.sha256(original).hexdigest(),
                "edits": [{"old": args.old, "new": args.new}],
            }
        ],
    }
    # Emit one complete JSON document; the host owns validation and all writes.
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
