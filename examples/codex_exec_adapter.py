"""Run a protocol-2 TODO Flow worker with configured ``codex exec`` sandbox."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from todo_flow.change_proposal import prepare_changes  # noqa: E402
from todo_flow.language import output_instruction  # noqa: E402
from todo_flow.native_proposal import _check_schema, _constant, _object  # noqa: E402
from todo_flow import repositories  # noqa: E402
from todo_flow.worker import (  # noqa: E402
    codex_exec_base_args,
    codex_schema,
    codex_sandbox_from_environment,
    validate,
    worker_instructions,
)

KINDS = {"assess", "work", "review", "triage", "watch"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _text(value, label):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"Invalid {label}")
    return value


def _directory(value, label):
    path = Path(_text(value, label))
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Missing {label}") from error
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory")
    return resolved


def validate_context(context):
    if type(context) is not dict or type(context.get("worker_protocol")) is not int:
        raise ValueError("Expected a protocol-2 worker context object")
    if context["worker_protocol"] != 2:
        raise ValueError("Only worker_protocol=2 is supported")
    task = context.get("task")
    if (
        type(task) is not dict
        or task.get("kind") not in KINDS
        or not isinstance(task.get("id"), str)
        or not isinstance(task.get("purpose"), str)
    ):
        raise ValueError("Context task must be assess, work, review, triage or watch")
    if type(context.get("paths")) is not dict or any(
        not isinstance(value, str) or not value for value in context["paths"].values()
    ):
        raise ValueError("Context paths must map names to nonempty paths")
    if not GIT_OID.fullmatch(context.get("head", "")):
        raise ValueError("Context requires a full Git HEAD")
    workspace = _directory(context.get("workspace"), "workspace")

    declarations = context.get("repositories")
    if declarations is None:
        patterns = context.get("writable_patterns")
        if not isinstance(patterns, list) or any(not isinstance(item, str) for item in patterns):
            raise ValueError("Single-repository context requires writable_patterns")
        return workspace, None
    if type(declarations) is not dict or len(declarations) < 2:
        raise ValueError("Multi-repository context requires at least two named repositories")

    heads = {}
    resolved_workspaces = set()
    for name, row in declarations.items():
        if not isinstance(name, str) or not name or ":" in name or type(row) is not dict:
            raise ValueError("Invalid named repository context")
        repo_path = _directory(row.get("path"), f"{name} repository")
        repo_workspace = _directory(row.get("workspace"), f"{name} workspace")
        head = row.get("head")
        patterns = row.get("writable_patterns")
        if (
            not repo_path.is_dir()
            or not GIT_OID.fullmatch(head or "")
            or not isinstance(patterns, list)
            or any(not isinstance(item, str) or not item for item in patterns)
        ):
            raise ValueError(f"Invalid repository identity for {name}")
        if repo_workspace in resolved_workspaces:
            raise ValueError("Repository workspaces must be distinct")
        resolved_workspaces.add(repo_workspace)
        heads[name] = head

    # Stored contexts are written with sorted keys, so declaration order does not
    # identify the primary repository. The primary is the one whose workspace is
    # the context workspace.
    primary = [
        name
        for name, row in declarations.items()
        if str(_directory(row["workspace"], f"{name} workspace")) == str(workspace)
    ]
    repository_heads = context.get("repository_heads")
    if (
        len(primary) != 1
        or type(repository_heads) is not dict
        or repository_heads != heads
        or context.get("head") != heads[primary[0]]
        or context.get("aggregate_head") != repositories.fingerprint(heads)
    ):
        raise ValueError("Repository heads, primary workspace or aggregate generation changed")
    return workspace, declarations


def _schema_response(text, kind, context):
    result = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    _check_schema(result, codex_schema())
    result = {key: value for key, value in result.items() if value is not None}
    if result.get("question") and any(result.get(k) for k in ("changes", "next", "publish")):
        # Work was produced alongside a question: keep the work for review and
        # carry the question as text, since a decision wait cannot carry work.
        result["summary"] = (
            (result.get("summary") or "") + "\nOpen question: " + result.pop("question")
        )
    if result.get("question"):
        # A question pauses the track; carry observations as text instead of
        # side-effecting watches or findings, which the engine rejects.
        notes = [
            json.dumps(result.pop(key), ensure_ascii=False)
            for key in ("watches", "findings", "triage")
            if result.get(key)
        ]
        for key in ("watches", "findings", "triage"):
            result.pop(key, None)
        if notes:
            result["summary"] = (result.get("summary") or "") + "\n" + "\n".join(notes)
    result = validate(result, kind)
    changes = result.get("changes", [])
    if not changes:
        return result

    if context.get("repositories") is None:
        prepare_changes(
            context["workspace"],
            changes,
            context["writable_patterns"],
            context["head"],
        )
        return result

    routed = repositories.route_changes(
        {"repos": [{"name": name} for name in context["repositories"]]}, changes
    )
    for name, rows in routed.items():
        repo = context["repositories"][name]
        prepare_changes(repo["workspace"], rows, repo["writable_patterns"], repo["head"])
    return result


def _prompt(context, sandbox):
    language = context.get("language", "en")
    return (
        worker_instructions(sandbox)
        + "\nNever inspect secret material, including .env files, API keys, tokens, credentials, or shell history.\n"
        + "\n"
        + output_instruction(language)
        + "\nReturn optional fields as null when unused."
        + "\nWhen you return a question, set triage, watches and findings to null;"
        + " put what you observed in summary instead.\n"
        + _references()
        + "TASK CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def _references():
    # Operator-supplied read-only locations outside the task workspaces, such as
    # untracked plans or execution reports the track cites.
    roots = [
        item for item in os.environ.get("TODO_FLOW_REFERENCE_DIRS", "").split(os.pathsep) if item
    ]
    if not roots:
        return ""
    return (
        "Read-only reference locations outside the workspaces (read them; never write there): "
        + ", ".join(roots)
        + "\n"
    )


def _positive_timeout(value):
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds")
    return seconds


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _run_codex(args, workspace, declarations, prompt, schema_path, output_path, sandbox, tmpdir):
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("codex executable was not found on PATH")
    command = codex_exec_base_args(executable, sandbox=sandbox)
    if declarations:
        for name, row in declarations.items():
            candidate = _directory(row["workspace"], f"{name} workspace")
            if candidate != workspace:
                command.extend(("--add-dir", str(candidate)))
    command.extend(
        (
            "--model",
            args.model,
            "--config",
            "model_reasoning_effort=" + json.dumps(args.effort),
            "--cd",
            str(workspace),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
        )
    )

    child = {"process": None}

    def stop_on_signal(signum, _frame):
        if child["process"] is not None and child["process"].poll() is None:
            _kill_group(child["process"])
        raise SystemExit(128 + signum)

    previous_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, stop_on_signal)
    try:
        env = dict(os.environ)
        if tmpdir is not None:
            env["TMPDIR"] = str(tmpdir)
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            env=env,
        )
        child["process"] = process
        try:
            stdout, stderr = process.communicate(prompt, timeout=args.timeout)
        except subprocess.TimeoutExpired as error:
            _kill_group(process)
            detail = error.stderr or ""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            if detail:
                sys.stderr.write(detail)
            raise TimeoutError(f"codex timed out after {args.timeout:g} seconds") from None
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if stderr:
        sys.stderr.write(stderr)
    if process.returncode != 0:
        if stdout:
            sys.stderr.write(stdout[-2000:])
        raise RuntimeError(f"codex exited with status {process.returncode}")
    if not output_path.is_file():
        raise RuntimeError("codex exited successfully without writing its final proposal")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=sorted(EFFORTS), required=True)
    parser.add_argument("--timeout", type=_positive_timeout, default=1800)
    parser.add_argument(
        "--tmp-dir", type=Path, help="Directory for temporary schema and response files"
    )
    args = parser.parse_args(argv)
    if "\0" in args.model or not args.model.strip():
        parser.error("model must be nonempty and contain no NUL")

    try:
        context = json.load(sys.stdin, object_pairs_hook=_object, parse_constant=_constant)
        workspace, declarations = validate_context(context)
        sandbox = codex_sandbox_from_environment()
        tmpdir = os.environ.get("TMPDIR") if sandbox == "workspace-write" else None
        if sandbox == "workspace-write" and (not tmpdir or not Path(tmpdir).is_absolute()):
            raise ValueError("workspace-write requires TODO Flow to provide an attempt TMPDIR")
        temp_root = args.tmp_dir or (tmpdir if tmpdir is not None else Path(tempfile.gettempdir()))
        if args.tmp_dir and args.tmp_dir.is_symlink():
            raise ValueError("tmp-dir must not be a symlink")
        temp_root = temp_root.resolve(strict=True)
        if not temp_root.is_dir():
            raise ValueError("tmp-dir must be an existing directory")
        with tempfile.TemporaryDirectory(prefix="todo-flow-codex-", dir=temp_root) as folder:
            folder = Path(folder)
            schema_path = folder / "schema.json"
            output_path = folder / "proposal.json"
            schema_path.write_text(json.dumps(codex_schema(), ensure_ascii=False), encoding="utf-8")
            _run_codex(
                args,
                workspace,
                declarations,
                _prompt(context, sandbox),
                schema_path,
                output_path,
                sandbox,
                tmpdir,
            )
            result = _schema_response(
                output_path.read_text(encoding="utf-8"), context["task"]["kind"], context
            )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except (OSError, RecursionError, RuntimeError, TimeoutError, UnicodeError, ValueError) as error:
        print(f"codex_exec_adapter: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
