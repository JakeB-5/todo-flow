"""Text contracts for the tag-only release workflow and documented provenance checks.

These tests read the workflow and update guides as text. They neither sign nor verify a real
attestation; live signing and `gh attestation verify` need an approved release or fixture.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
GUIDES = ("UPDATES.md", "UPDATES.ko.md", "UPDATES.ja.md", "UPDATES.zh-CN.md")
ATTEST_SHA = "1e69f48acb82d1966a394da916b4c1698aa569d6"
SUBJECTS = ["dist/release/todo_flow-*.whl", "dist/release/todo_flow-*.tar.gz"]
CHECKSUMS = "dist/release/SHA256SUMS"
JOB_WRITES = {"attestations", "contents", "id-token"}
SOURCE_CHECKS = ("git rev-parse HEAD", "${{ github.sha }}", "pyproject.toml", "--verify-tag")
VERIFY_POLICY = (
    r"--repo JakeB-5/todo-flow(?=\s|$)",
    r"--signer-workflow JakeB-5/todo-flow/\.github/workflows/release\.yml(?=\s|$)",
    r"--source-ref refs/tags/v\S+",
    r"--deny-self-hosted-runners(?=\s|$)",
)
FORBIDDEN = (
    "--clobber",
    "gh release upload",
    "gh release edit",
    "gh release delete",
    "overwrite",
)
CUSTOM_PREDICATE_INPUTS = (
    "sbom-path:",
    "predicate-type:",
    "predicate:",
    "predicate-path:",
    "push-to-registry:",
)


def indent(line):
    return len(line) - len(line.lstrip())


def code_lines(text):
    lines = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            lines.append(line)
    return lines


def top_level(text, key):
    block = []
    for line in code_lines(text):
        if not line[0].isspace():
            if block:
                break
            if line.split(":", 1)[0] == key:
                block.append(line)
        elif block:
            block.append(line)
    return block


def continued(lines):
    """Join shell lines continued with a trailing backslash."""
    commands = []
    current = ""
    for line in lines:
        stripped = line.strip()
        if stripped.endswith("\\"):
            current += stripped[:-1].strip() + " "
            continue
        commands.append(current + stripped)
        current = ""
    if current:
        commands.append(current.strip())
    return commands


def subject_paths(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "subject-path: |":
            continue
        paths = []
        for item in lines[index + 1 :]:
            if not item.strip():
                continue
            if indent(item) <= indent(line):
                break
            paths.append(item.strip())
        return paths
    return []


def release_commands(text):
    commands = []
    for command in continued(code_lines(text)):
        if "gh release create" in command:
            commands.append(command)
    return commands


def uploaded_paths(command):
    paths = []
    for token in command.split():
        token = token.strip("'\"")
        if token.startswith("dist/"):
            paths.append(token)
    return paths


def permission_problems(lines):
    problems = []
    granted = set()
    in_jobs = False
    job_permissions = False
    for line in lines:
        depth = indent(line)
        stripped = line.strip()
        if depth == 0:
            in_jobs = stripped == "jobs:"
        if depth <= 4:
            job_permissions = in_jobs and depth == 4 and stripped == "permissions:"
        if "write-all" in stripped:
            problems.append("permissions: write-all is not allowed")
        match = re.match(r"([a-z-]+):\s*write\b", stripped)
        if not match:
            continue
        if job_permissions and depth == 6:
            granted.add(match.group(1))
        else:
            problems.append(f"permissions: {match.group(1)}: write outside the release job")
    if granted != JOB_WRITES:
        problems.append(f"permissions: job grants {sorted(granted)}")
    return problems


def attestation_problems(text, lines):
    problems = []
    uses = [line.strip() for line in lines if "actions/attest" in line]
    if uses != [f"uses: actions/attest@{ATTEST_SHA}  # v4"]:
        problems.append("attest: use one actions/attest step pinned to the v4 commit")
    for name in CUSTOM_PREDICATE_INPUTS:
        if any(line.strip().startswith(name) for line in lines):
            problems.append(f"attest: {name} replaces the default SLSA provenance")
    subjects = subject_paths(text)
    if not subjects or any(not path.startswith("dist/release/") for path in subjects):
        problems.append("subject: attest only files built in dist/release")
    if "uv build --out-dir dist/release" not in text:
        problems.append("subject: build the tagged commit into dist/release")
    commands = release_commands(text)
    uploads = set(uploaded_paths(commands[0])) if len(commands) == 1 else set()
    if uploads - {CHECKSUMS} != set(subjects):
        problems.append("subject: release files must be exactly the attested files")
    return problems


def workflow_problems(text):
    problems = []
    lines = code_lines(text)
    code = "\n".join(lines)
    lowered = code.lower()
    if top_level(text, "on") != ["on:", "  push:", "    tags: ['v*']"]:
        problems.append("trigger: only v* tag pushes may start a release")
    for event in ("pull_request", "workflow_run"):
        if event in lowered:
            problems.append(f"trigger: {event} must not start a release")
    if top_level(text, "permissions") != ["permissions:", "  contents: read"]:
        problems.append("permissions: the top level must grant only contents: read")
    problems.extend(permission_problems(lines))
    problems.extend(attestation_problems(text, lines))
    for word in FORBIDDEN:
        if word in lowered:
            problems.append(f"immutability: {word} is not allowed")
    view = lowered.find("gh release view")
    if view < 0 or view > lowered.find("uv build"):
        problems.append("immutability: refuse an existing release before building")
    for required in SOURCE_CHECKS:
        if required not in code:
            problems.append(f"source: {required} check is missing")
    if "secrets." in lowered or re.search(r"\bset -[a-z]*x", lowered):
        problems.append("secrets: do not reference secrets or trace shell commands")
    return problems


def verify_commands(text):
    commands = []
    block = None
    for line in text.splitlines():
        if not line.startswith("```"):
            if block is not None:
                block.append(line)
            continue
        if block is None:
            block = []
            continue
        for command in continued(block):
            if command.startswith("gh attestation verify"):
                commands.append(command)
        block = None
    return commands


def guide_problems(text):
    commands = verify_commands(text)
    if not commands:
        return ["guide: no gh attestation verify command"]
    problems = []
    for command in commands:
        for pattern in VERIFY_POLICY:
            if not re.search(pattern, command):
                problems.append(f"guide: {pattern} is missing from {command}")
    return problems


class ReleaseWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def mutate(self, old, new, after=""):
        start = self.workflow.index(after) if after else 0
        index = self.workflow.index(old, start)
        return self.workflow[:index] + new + self.workflow[index + len(old) :]

    def assert_rejected(self, text, prefix):
        problems = workflow_problems(text)
        self.assertTrue(any(problem.startswith(prefix) for problem in problems), problems)

    def test_release_workflow_satisfies_contract(self):
        self.assertEqual(workflow_problems(self.workflow), [])
        self.assertEqual(subject_paths(self.workflow), SUBJECTS)
        commands = release_commands(self.workflow)
        self.assertEqual(uploaded_paths(commands[0]), SUBJECTS + [CHECKSUMS])

    def test_untrusted_and_branch_triggers_are_rejected(self):
        tags = "    tags: ['v*']\n"
        for extra in (
            "  pull_request:\n",
            "  pull_request_target:\n",
            "    branches: [main]\n",
            "  workflow_dispatch:\n",
        ):
            with self.subTest(extra=extra.strip()):
                self.assert_rejected(self.mutate(tags, tags + extra), "trigger:")

    def test_write_permissions_outside_the_release_job_are_rejected(self):
        top = "permissions:\n  contents: read\n"
        job = "      attestations: write\n"
        cases = (
            self.mutate(top, "permissions:\n  contents: write\n"),
            self.mutate(top, top + "  id-token: write\n"),
            self.mutate(top, "permissions: write-all\n"),
            self.mutate(job, job + "      artifact-metadata: write\n"),
            self.mutate(job, ""),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(text, "permissions:")

    def test_unpinned_or_custom_attestations_are_rejected(self):
        pinned = f"uses: actions/attest@{ATTEST_SHA}"
        subject = "          subject-path: |\n"
        cases = (
            self.mutate(pinned, "uses: actions/attest@v4"),
            self.mutate(pinned, "uses: actions/attest-build-provenance@v4"),
            self.mutate(subject, "          sbom-path: sbom.json\n" + subject),
            self.mutate(subject, "          predicate-type: custom\n" + subject),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(text, "attest:")

    def test_release_files_must_be_the_attested_files(self):
        extra = CHECKSUMS + " dist/ci/extra.whl"
        cases = (
            self.mutate(SUBJECTS[0], "dist/other/todo_flow-*.whl", after="subject-path"),
            self.mutate(SUBJECTS[1], "dist/ci/todo_flow-*.tar.gz", after="gh release create"),
            self.mutate(CHECKSUMS, extra, after="gh release create"),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(text, "subject:")

    def test_overwriting_or_rerunning_an_existing_release_is_rejected(self):
        create = 'gh release create "$TAG"'
        cases = (
            self.mutate("--verify-tag", "--verify-tag --clobber"),
            self.mutate(create, 'gh release upload "$TAG" --clobber'),
            self.mutate("gh release view", "echo skipped"),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(text, "immutability:")

    def test_unchecked_tag_commit_or_version_is_rejected(self):
        for old in ("git rev-parse HEAD", "pyproject.toml", "--verify-tag"):
            with self.subTest(removed=old):
                self.assert_rejected(self.mutate(old, "true"), "source:")

    def test_secret_references_and_shell_tracing_are_rejected(self):
        token = "GH_TOKEN: ${{ github.token }}"
        cases = (
            self.mutate(token, "GH_TOKEN: ${{ secrets.RELEASE_TOKEN }}"),
            self.mutate("set -euo pipefail", "set -euxo pipefail"),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(text, "secrets:")


class ConsumerVerificationGuideTests(unittest.TestCase):
    def test_each_guide_pins_repository_signer_workflow_and_tag_ref(self):
        for name in GUIDES:
            with self.subTest(guide=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertEqual(len(verify_commands(text)), 2)
                self.assertEqual(guide_problems(text), [])

    def test_weakened_verification_policy_is_rejected(self):
        text = (ROOT / "UPDATES.md").read_text(encoding="utf-8")
        cases = []
        for line in (
            "  --repo JakeB-5/todo-flow \\\n",
            "  --signer-workflow JakeB-5/todo-flow/.github/workflows/release.yml \\\n",
            "  --source-ref refs/tags/vX.Y.Z \\\n",
            "  --deny-self-hosted-runners\n",
        ):
            self.assertIn(line, text)
            cases.append(text.replace(line, ""))
        cases.append(text.replace("--repo JakeB-5/todo-flow ", "--owner JakeB-5 "))
        cases.append(text.replace("--repo JakeB-5/todo-flow ", "--repo other/todo-flow "))
        cases.append(text.replace("refs/tags/vX.Y.Z", "refs/heads/main"))
        cases.append(text.replace("gh attestation verify", "gh attestation inspect"))
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                self.assertTrue(guide_problems(case))


if __name__ == "__main__":
    unittest.main()
