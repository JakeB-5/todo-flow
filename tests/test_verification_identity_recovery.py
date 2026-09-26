import base64
import json
import tempfile
import unittest
from pathlib import Path

from todo_flow.store import Store


class VerificationIdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "todo"
        self.store = Store(self.root)
        self.store.configure({"language": "en"})
        self.config_path = self.root / "config/1.json"
        self.pending = self.root / ".pending.json"
        self.marker = self.root / "recovery-marker.txt"
        self.marker.write_text("before")

    def files(self):
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def journal(self, config, binary=False):
        catalog = json.loads((self.root / ".catalog.json").read_text())
        catalog["version"] = "identity-recovery-test"
        text = json.dumps({"id": 1, "body": config})
        if binary:
            text = {"base64": base64.b64encode(text.encode()).decode("ascii")}
        self.pending.write_text(
            json.dumps(
                {
                    "catalog": catalog,
                    "writes": {
                        "recovery-marker.txt": "after",
                        "config/1.json": text,
                    },
                }
            )
        )

    def test_unsupported_pending_config_preserves_every_file(self):
        for binary in (False, True):
            with self.subTest(binary=binary):
                self.journal({"verify_identity": {"version": 999}}, binary=binary)
                before = self.files()
                with self.assertRaisesRegex(ValueError, "identity"):
                    Store(self.root)
                self.assertEqual(self.files(), before)
                self.assertEqual(self.marker.read_text(), "before")

    def test_existing_unsupported_config_cannot_be_overwritten_by_recovery(self):
        self.config_path.write_text(
            json.dumps({"id": 1, "body": {"verify_identity": {"version": 999}}})
        )
        self.journal({"language": "en"})
        before = self.files()
        with self.assertRaisesRegex(ValueError, "identity"):
            Store(self.root)
        self.assertEqual(self.files(), before)

    def test_existing_store_rechecks_pending_config_before_connect(self):
        self.journal({"verify_identity": {"version": 999}})
        before = self.files()
        with self.assertRaisesRegex(ValueError, "identity"):
            self.store.config()
        self.assertEqual(self.files(), before)

    def test_unsupported_config_without_journal_is_rejected_without_new_files(self):
        self.config_path.write_text(
            json.dumps({"id": 1, "body": {"verify_identity": {"version": 999}}})
        )
        (self.root / ".store.lock").unlink()
        before = self.files()
        with self.assertRaisesRegex(ValueError, "identity"):
            Store(self.root)
        self.assertEqual(self.files(), before)

    def test_legacy_and_supported_pending_config_recover(self):
        configs = [
            {"language": "en"},
            {
                "verify_identity": {
                    "version": 1,
                    "files": ["missing-runner.py"],
                    "environment": ["VERIFY_TOKEN"],
                    "nonce": "rotation-2",
                }
            },
        ]
        for config in configs:
            with self.subTest(config=config):
                self.journal(config)
                recovered = Store(self.root)
                self.assertEqual(recovered.config(), config)
                self.assertEqual(self.marker.read_text(), "after")
                self.assertFalse(self.pending.exists())

    def test_invalid_new_config_does_not_leave_a_poisoned_journal(self):
        fresh = Store(Path(self.tmp.name) / "fresh")
        before = fresh.files.catalog.read_bytes()
        with self.assertRaisesRegex(ValueError, "identity"):
            fresh.configure({"verify_identity": {"version": 999}})
        self.assertFalse(fresh.files.pending.exists())
        self.assertFalse((fresh.path / "config/1.json").exists())
        self.assertEqual(fresh.files.catalog.read_bytes(), before)
        fresh.configure({"language": "en"})
        self.assertEqual(fresh.config(), {"language": "en"})
