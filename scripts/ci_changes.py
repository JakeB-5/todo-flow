"""Select expensive CI from content changes, keeping version-only releases fast."""

import argparse
import re
import subprocess
import tomllib
from pathlib import PurePosixPath


UPDATE_INPUTS = {
    "pyproject.toml",
    "uv.lock",
    "src/todo_flow/release.py",
    "src/todo_flow/release.json",
    "src/todo_flow/engine_updates.py",
    "src/todo_flow/maintenance.py",
    "src/todo_flow/skills.py",
    "scripts/update_smoke.py",
}


def normalized(path, content):
    if path == "pyproject.toml":
        value = tomllib.loads(content)
        value["project"].pop("version", None)
        return value
    if path == "uv.lock":
        value = tomllib.loads(content)
        for package in value.get("package", []):
            if package.get("name") == "todo-flow" and package.get("source") == {"editable": "."}:
                package.pop("version", None)
        return value
    if path == "src/todo_flow/release.py":
        return re.sub(r'(?m)^(\s*VERSION = )"[0-9]+\.[0-9]+\.[0-9]+"$', r'\1"VERSION"', content)
    return content


def classify(changes):
    tests = updates = False
    for path, before, after in changes:
        parts = PurePosixPath(path).parts
        if (len(parts) == 1 and path.endswith(".md")) or parts[0] in {"assets", "docs"}:
            continue
        if before is not None and after is not None:
            try:
                if normalized(path, before) == normalized(path, after):
                    continue
            except (ValueError, KeyError, TypeError):
                pass  # Changed or unreadable metadata gets the normal checks.
        tests = True
        updates |= path in UPDATE_INPUTS or parts[0] == "skills"
    return tests, updates


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def content(revision, path):
    result = subprocess.run(["git", "show", f"{revision}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    if not args.base or set(args.base) == {"0"}:
        tests, updates = True, True
    else:
        paths = git("diff", "--name-only", "--no-renames", "-z", args.base, args.head).split("\0")
        tests, updates = classify(
            (path, content(args.base, path), content(args.head, path)) for path in paths if path
        )
    print(f"tests={str(tests).lower()}")
    print(f"updates={str(updates).lower()}")


if __name__ == "__main__":
    main()
