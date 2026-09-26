import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_flow import cli
from todo_flow.store import Store


class VerificationIdentityConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "todo"

    def args(self, declaration=None):
        argv = [
            "--state",
            str(self.state),
            "init",
            "--repo",
            str(self.root),
            "--verify",
            '["python3", "verify.py"]',
            "--language",
            "en",
        ]
        if declaration is not None:
            argv += ["--verify-identity", declaration]
        return cli.parser().parse_args(argv)

    def files(self):
        return {
            path.relative_to(self.state).as_posix(): path.read_bytes()
            for path in self.state.rglob("*")
            if path.is_file()
        }

    def pending(self, store):
        catalog = json.loads(store.files.catalog.read_text())
        catalog["version"] = "configuration-boundary-test"
        store.files.pending.write_text(
            json.dumps({"catalog": catalog, "writes": {"marker.txt": "recovered"}})
        )

    def test_cli_rejects_invalid_declarations_before_constructing_store(self):
        invalid = [
            "{",
            "null",
            "[]",
            '{"version": 999}',
            '{"version": true}',
            '{"version": 1, "files": "runner.py"}',
            '{"version": 1, "environment": ["TOKEN=value"]}',
            '{"version": 1, "nonce": 2}',
            '{"version": 1, "unknown": []}',
        ]
        for declaration in invalid:
            with self.subTest(declaration=declaration):
                args = self.args(declaration)
                with patch("todo_flow.cli.command"), patch("todo_flow.cli.Store") as store:
                    with self.assertRaises(ValueError):
                        cli.initialize(args)
                store.assert_not_called()
                self.assertFalse(self.state.exists())

    def test_invalid_cli_input_preserves_pending_recovery(self):
        store = Store(self.state)
        self.pending(store)
        before = self.files()
        with patch("todo_flow.cli.command"):
            with self.assertRaisesRegex(ValueError, "identity"):
                cli.initialize(self.args('{"version": 999}'))
        self.assertEqual(self.files(), before)
        self.assertFalse((self.state / "marker.txt").exists())

    def test_cli_persists_files_environment_names_and_nonce(self):
        declaration = {
            "version": 1,
            "files": ["tools/verify.py", str(self.root / "external-runner.py")],
            "environment": ["VERIFY_TOKEN", "VERIFY_MODE"],
            "nonce": "rotation-2",
        }
        output = io.StringIO()
        with patch("todo_flow.cli.command"), contextlib.redirect_stdout(output):
            cli.initialize(self.args(json.dumps(declaration)))
        config = Store(self.state).config()
        self.assertEqual(config["verify_identity"], declaration)
        self.assertEqual(config["verify"], ["python3", "verify.py"])
        self.assertEqual(json.loads(output.getvalue())["config"], config)
        record = json.loads((self.state / "config/1.json").read_text())
        self.assertEqual(record["body"]["verify_identity"], declaration)

    def test_cli_without_declaration_keeps_legacy_configuration_supported(self):
        with patch("todo_flow.cli.command"), contextlib.redirect_stdout(io.StringIO()):
            cli.initialize(self.args())
        config = Store(self.state).config()
        self.assertNotIn("verify_identity", config)
        self.assertEqual(config["verify"], ["python3", "verify.py"])

    def test_configure_rejects_invalid_input_before_transaction(self):
        store = Store(self.state)
        with patch.object(store, "transaction") as transaction:
            with self.assertRaisesRegex(ValueError, "identity"):
                store.configure({"verify_identity": {"version": 999}})
        transaction.assert_not_called()

    def test_invalid_configure_does_not_apply_pending_recovery(self):
        store = Store(self.state)
        self.pending(store)
        before = self.files()
        with self.assertRaisesRegex(ValueError, "identity"):
            store.configure({"verify_identity": {"version": 999}})
        self.assertEqual(self.files(), before)
        self.assertFalse((self.state / "marker.txt").exists())
        store.configure({"language": "en", "verify_identity": {"version": 1}})
        self.assertFalse(store.files.pending.exists())
        self.assertEqual((self.state / "marker.txt").read_text(), "recovered")
        self.assertEqual(store.config()["verify_identity"], {"version": 1})
