"""Exercise real uv-tool upgrades in isolated directories, without models or remote effects.

Build the current source distribution first, then:
uv run python scripts/update_smoke.py --root /absolute/new-directory
"""

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

from todo_flow.release import VERSION, release_number


def run(args, env=None, cwd=None, ok=True):
    result = subprocess.run(list(map(str, args)), env=env, cwd=cwd, text=True, capture_output=True)
    if ok and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--artifacts", help="Directory containing the current wheel and sdist")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if root.exists():
        parser.error("Use a new directory; existing files are preserved")
    root.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    base_version = VERSION
    major, minor, patch = release_number(base_version)
    next_version = f"{major}.{minor}.{patch + 1}"
    broken_version = f"{major}.{minor}.{patch + 2}"
    artifacts = Path(args.artifacts).resolve() if args.artifacts else source / "dist" / base_version
    wheels = {base_version: artifacts / f"todo_flow-{base_version}-py3-none-any.whl"}
    for version in (next_version, broken_version):
        directory = root / ("source-" + version)
        directory.mkdir()
        with tarfile.open(artifacts / f"todo_flow-{base_version}.tar.gz") as archive:
            archive.extractall(directory, filter="data")
        checkout = directory / f"todo_flow-{base_version}"
        project = checkout / "pyproject.toml"
        project.write_text(
            project.read_text().replace(f'version = "{base_version}"', f'version = "{version}"', 1)
        )
        skill = checkout / "skills/todo/SKILL.md"
        skill.write_text(skill.read_text() + "\nTest release: use the current project language.\n")
        if version == broken_version:
            cli = checkout / "src/todo_flow/cli.py"
            cli.write_text(
                'raise RuntimeError("Deliberately broken release fixture")\n' + cli.read_text()
            )
        run(["uv", "build", "--wheel", "--out-dir", root / "wheels"], cwd=checkout)
        wheels[version] = root / "wheels" / f"todo_flow-{version}-py3-none-any.whl"
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
        "TODO_FLOW_HOME": str(root / "control"),
    }
    run(["uv", "tool", "install", wheels[base_version]], env)
    cli = root / "bin/todo-flow"
    repo = root / "project"
    repo.mkdir()
    run(["git", "init", "-b", "main"], cwd=repo)
    run(
        [
            "git",
            "-c",
            "user.name=Update Test",
            "-c",
            "user.email=update@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Fixture",
        ],
        cwd=repo,
    )
    state = root / "state"
    run(
        [
            cli,
            "--state",
            state,
            "init",
            "--repo",
            repo,
            "--language",
            "ko",
            "--verify",
            '["python3","-m","unittest"]',
        ],
        env,
    )
    skills = repo / ".agents/skills"
    run([cli, "--state", state, "install-skills", "--target", skills], env)
    config_before = (state / "config/1.json").read_bytes()
    context_before = (skills / "todo/project.json").read_bytes()
    doc = {
        "id": "upgrade-fixture",
        "title": "Upgrade fixture",
        "goal": "Preserve this track",
        "scope": "Upgrade test only",
        "evidence": "Synthetic local fixture",
        "conditions": [{"id": "retained", "text": "Document survives", "method": "Compare bytes"}],
    }
    draft = root / "track.json"
    draft.write_text(json.dumps(doc))
    run([cli, "--state", state, "register", draft], env)
    document = state / "tracks/upgrade-fixture/track.html"
    document_before = document.read_bytes()
    run([cli, "--state", state, "start", doc["id"]], env)

    def canonical_digest():
        return {
            str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in state.rglob("*")
            if p.is_file() and ".cache" not in p.relative_to(state).parts
        }

    state_before = canonical_digest()
    server = subprocess.Popen(
        [str(cli), "--state", str(state), "serve", "--port", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout.readline().startswith("Dashboard:")
        blocked = run([cli, "upgrade", "--wheel", wheels[next_version]], env, ok=False)
        assert blocked.returncode != 0 and "active runtime" in blocked.stderr, blocked.stderr
    finally:
        server.terminate()
        server.communicate(timeout=5)
    plan = json.loads(
        run([cli, "upgrade", "--wheel", wheels[next_version], "--dry-run"], env).stdout
    )
    assert plan["dryRun"] and plan["version"] == next_version
    upgraded = json.loads(run([cli, "upgrade", "--wheel", wheels[next_version]], env).stdout)
    assert run([cli, "--version"], env).stdout.strip() == f"todo-flow {next_version}"
    run([cli, "--state", state, "compatibility", "--target", skills], env)
    updated_skills = json.loads(run([cli, "update-skills", "--target", skills], env).stdout)
    assert (skills / "todo/project.json").read_bytes() == context_before
    assert "Test release:" in (skills / "todo/SKILL.md").read_text()
    run([cli, "update-skills", "--target", skills, "--rollback", updated_skills["backup"]], env)
    assert "Test release:" not in (skills / "todo/SKILL.md").read_text()
    run([cli, "upgrade", "--rollback", upgraded["backup"]], env)
    assert run([cli, "--version"], env).stdout.strip() == f"todo-flow {base_version}"
    failed = run([cli, "upgrade", "--wheel", wheels[broken_version]], env, ok=False)
    assert failed.returncode != 0 and "Deliberately broken" in failed.stderr, failed.stderr
    assert run([cli, "--version"], env).stdout.strip() == f"todo-flow {base_version}"
    assert not (root / "control/engine-pending.json").exists()
    # Simulate a stop after installation but before the commit marker, with the CLI missing.
    interrupted = json.loads(run([cli, "upgrade", "--wheel", wheels[next_version]], env).stdout)
    recovery = root / "control/engine-updates" / interrupted["backup"]
    receipt = json.loads((recovery / "receipt.json").read_text())
    receipt["phase"] = "prepared"
    (recovery / "receipt.json").write_text(json.dumps(receipt))
    (root / "control/engine-pending.json").write_text(json.dumps({"id": interrupted["backup"]}))
    (root / "tools/todo-flow/bin/todo-flow").unlink()
    recovery_env = {
        key: value
        for key, value in env.items()
        if key not in ("TODO_FLOW_HOME", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR")
    }
    run([receipt["python"], recovery / "engine_updates.py", "--recover"], recovery_env)
    assert run([cli, "--version"], env).stdout.strip() == f"todo-flow {base_version}"
    assert (state / "config/1.json").read_bytes() == config_before
    after = json.loads(run([cli, "--state", state, "status"], env).stdout)
    assert after["tasks"] and all(task["status"] == "queued" for task in after["tasks"])
    assert canonical_digest() == state_before
    assert document.read_bytes() == document_before
    assert (skills / "todo/project.json").read_bytes() == context_before
    report = {
        "isolated": True,
        "engineUpgrade": f"{base_version} -> {next_version}",
        "activeDashboardBlocked": True,
        "skillUpdateAndRollback": True,
        "engineRollback": True,
        "brokenReleaseAutoRollback": True,
        "missingCliRecovered": True,
        "projectConfigAndDocumentPreserved": True,
        "pendingWorkPreserved": True,
        "canonicalStatePreserved": True,
        "documentDigest": hashlib.sha256(document_before).hexdigest(),
        "modelCalls": 0,
        "remoteMutations": 0,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
