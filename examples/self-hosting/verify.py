"""Verify the assigned checkout independently of the installed TODO Flow runtime."""

import os
from pathlib import Path
import subprocess

workspace = Path.cwd().resolve()
environment = dict(os.environ)
environment.pop("PYTHONPATH", None)
environment.pop("PYTHONHOME", None)
environment["UV_PROJECT_ENVIRONMENT"] = str(workspace / ".venv")
commands = [
    ["uv", "sync", "--frozen"],
    ["uv", "run", "--frozen", "python", "-m", "unittest", "discover", "-s", "tests", "-v"],
    ["uv", "run", "--frozen", "ruff", "check", "src", "tests", "scripts"],
    ["uv", "run", "--frozen", "ruff", "format", "--check", "src", "tests", "scripts"],
    ["node", "--check", "src/todo_flow/web/app.js"],
    ["node", "--test", "tests/dashboard_i18n.test.cjs"],
    ["uv", "build"],
]
for command in commands:
    print("Running: " + " ".join(command), flush=True)
    subprocess.run(command, cwd=workspace, env=environment, check=True)
print("All project verification checks passed.", flush=True)
