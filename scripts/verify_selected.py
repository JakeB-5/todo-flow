"""Execute only checks justified for the current Git tree, without a full-suite fallback."""

import argparse
import json
import os
from pathlib import Path
import subprocess


RESULT_SUFFIX = " TODO_FLOW_RESULT_V1"


def selected_plan(document, tree):
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise ValueError("Invalid verification selection format")
    if document["version"] != 1 or not isinstance(document.get("plans"), dict):
        raise ValueError("Unsupported verification selection format")
    plan = document["plans"].get(tree)
    if not isinstance(plan, dict):
        raise ValueError("No necessity assessment for the current Git tree; no checks executed")
    if not isinstance(plan.get("reason"), str) or not plan["reason"].strip():
        raise ValueError("The assessment must explain its scope and evidence")
    checks = plan.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Record explicit omissions instead of an empty assessment")
    for check in checks:
        if not isinstance(check, dict) or check.get("decision") not in {
            "necessary",
            "unnecessary",
            "undetermined",
        }:
            raise ValueError("Each check needs a necessity decision")
        for key in ("name", "reason"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                raise ValueError(f"Each check needs a nonempty {key}")
        if check["decision"] == "necessary":
            gap = check.get("evidence_gap")
            if not isinstance(gap, str) or not gap.strip():
                raise ValueError(
                    "A necessary check must explain why existing evidence is insufficient"
                )
            argv = check.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
            ):
                raise ValueError("A necessary check needs a nonempty argv array")
    return plan


def execute(plan, workspace):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["UV_PROJECT_ENVIRONMENT"] = str(workspace / ".venv")
    executed = omitted = 0
    uncertain = False
    print("Assessment: " + plan["reason"], flush=True)
    for check in plan["checks"]:
        if check["decision"] != "necessary":
            omitted += 1
            uncertain |= check["decision"] == "undetermined"
            print(f"SKIP {check['name']} ({check['decision']}): {check['reason']}", flush=True)
            continue
        print(f"RUN {check['name']}: {check['reason']}", flush=True)
        print("Evidence gap: " + check["evidence_gap"], flush=True)
        print("Command: " + json.dumps(check["argv"]), flush=True)
        # The enclosing TODO Flow verifier supervises this process and descendants.
        result = subprocess.run(check["argv"], cwd=workspace, env=environment, check=False)
        executed += 1
        if result.returncode:
            return (
                "inconclusive",
                f"{check['name']} exited {result.returncode}; later checks not run",
            )
    summary = f"Necessity assessment: {executed} checks executed; {omitted} omitted"
    if uncertain:
        return "inconclusive", summary + "; undetermined checks were not executed or marked passed"
    return "passed", summary + "; all selected checks succeeded (omissions are not test passes)"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", type=Path, required=True)
    options = parser.parse_args(argv)
    workspace = Path.cwd().resolve()
    try:
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=workspace, text=True
        ).strip()
        plan = selected_plan(json.loads(options.plans.read_text(encoding="utf-8")), tree)
        print("Verification selection for tree " + tree, flush=True)
        outcome, reason = execute(plan, workspace)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        outcome, reason = "inconclusive", str(error)
    print(json.dumps({"outcome": outcome, "reason": reason}) + RESULT_SUFFIX, flush=True)
    return 0 if outcome == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
