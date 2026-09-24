import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow.adapters import command
from todo_flow.cli import main
from todo_flow.documents import load, render_html
from todo_flow.language import select_language
from todo_flow.projections import Dashboard
from todo_flow.store import Store
from todo_flow.worker import run_worker


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "project"
        self.repo.mkdir()
        command(["git", "init", "-b", "main"], self.repo)
        command(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "Fixture",
            ],
            self.repo,
        )
        self.state = self.repo / "todo"

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, interactive=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch("sys.stdin.isatty", return_value=interactive):
            main(["--state", str(self.state), *args])
        return json.loads(out.getvalue().splitlines()[-1])

    def init(self, *args, **kwargs):
        return self.cli(
            "init",
            "--repo",
            str(self.repo),
            "--verify",
            '["python3","-m","unittest"]',
            *args,
            **kwargs,
        )

    def test_unattended_defaults_to_english_and_korean_is_explicit(self):
        self.init()
        self.assertEqual(Store(self.state).config()["language"], "en")
        self.state = self.repo / "korean-state"
        self.init("--language", "ko")
        overview = Dashboard(Store(self.state)).overview()
        self.assertEqual(overview["project"]["language"], "ko")
        self.assertNotIn(str(self.state), overview["project"]["key"])

    def test_interactive_language_selection_and_invalid_retry(self):
        with patch("builtins.input", side_effect=["invalid", "ko"]) as ask:
            self.init(interactive=True)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(Store(self.state).config()["language"], "ko")

    def test_explicit_language_never_prompts(self):
        with patch("builtins.input", side_effect=AssertionError("Unexpected prompt")):
            self.init("--language", "en", interactive=True)
        with self.assertRaises(ValueError):
            select_language("unsupported")

    def test_installed_skills_inherit_project_language_and_state(self):
        self.init("--language", "ko")
        target = self.repo / ".agents" / "skills"
        result = self.cli("install-skills", "--target", str(target))
        self.assertEqual(result["language"], "ko")
        for skill in target.iterdir():
            context = json.loads((skill / "project.json").read_text())
            self.assertEqual(context, {"language": "ko", "state": str(self.state.resolve())})
        self.assertTrue((target / "todo" / "assets" / "track.html").is_file())
        conflict_target = self.repo / "conflicting-skills"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.cli("install-skills", "--target", str(conflict_target), "--language", "en")
        self.assertFalse(conflict_target.exists())

    def test_standalone_skill_install_does_not_initialize_state(self):
        target = self.repo / ".claude" / "skills"
        self.cli("install-skills", "--target", str(target), "--language", "ko")
        self.assertFalse(self.state.exists())
        self.assertEqual(
            json.loads((target / "todo" / "project.json").read_text())["language"], "ko"
        )

    def test_legacy_config_defaults_and_project_preferences_are_isolated(self):
        first = Store(self.state)
        first.configure({"github": None, "endpoint": "review"})
        second = Store(self.repo / "other-state")
        second.configure({"github": None, "language": "ko"})
        a, b = Dashboard(first).overview(), Dashboard(second).overview()
        self.assertEqual(a["project"]["language"], "en")
        self.assertEqual(b["project"]["language"], "ko")
        self.assertNotEqual(a["project"]["key"], b["project"]["key"])

    def test_registered_json_uses_project_language_but_authored_html_is_preserved(self):
        self.init("--language", "ko")
        example = Path(__file__).resolve().parents[1] / "examples" / "retry-backoff.html"
        doc = load(example)
        source = self.root / "input.json"
        raw = {k: v for k, v in doc.items() if k not in ("presentation", "language")}
        source.write_text(json.dumps(raw))
        self.cli("register", str(source))
        saved = (self.state / "tracks" / raw["id"] / "track.html").read_text()
        self.assertIn('lang="ko"', saved)
        self.assertIn("완료 조건", saved)
        self.assertTrue(self.cli("register", str(source))["existing"])
        legacy = {**raw, "id": "legacy-language"}
        Store(self.state).register(legacy)
        source.write_text(json.dumps(legacy))
        self.assertTrue(self.cli("register", str(source))["existing"])
        self.assertNotIn("language", json.loads(Store(self.state).track(legacy["id"])["document"]))
        self.assertEqual(render_html(doc), example.read_text())
        english = render_html({**raw, "language": "en"})
        self.assertIn('lang="en"', english)
        self.assertIn("Acceptance conditions", english)

    def test_custom_worker_receives_language_without_mutating_original_context(self):
        script = self.root / "worker.py"
        script.write_text(
            "import json,sys\n"
            "from pathlib import Path\n"
            'Path("received.json").write_text(json.dumps(json.load(sys.stdin)))\n'
            'print(json.dumps({"summary":"Checked"}))\n'
        )
        original = {"goal": "Keep this text unchanged", "workspace": str(self.root)}
        run_worker(
            {
                "language": "ko",
                "worker_protocol": 2,
                "worker_launcher": "headless",
                "worker": {"type": "command", "argv": [sys.executable, str(script)]},
            },
            original,
            {"attempt": "locale", "kind": "assess"},
            self.root,
            lambda _: None,
        )
        received = json.loads((self.root / "received.json").read_text())
        self.assertEqual(received["language"], "ko")
        self.assertIn("한국어", received["output_language_instruction"])
        self.assertEqual(
            original, {"goal": "Keep this text unchanged", "workspace": str(self.root)}
        )
