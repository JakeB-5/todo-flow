"""Translation maintenance catches stale sources, broken navigation and missing releases."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_translations", ROOT / "scripts/check_translations.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class DocumentTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for stem in CHECK.GUIDES:
            for locale in CHECK.LOCALES:
                name = CHECK.guide_name(stem, locale)
                destinations = [CHECK.guide_name(stem, language) for language in CHECK.LOCALES]
                destinations += [
                    CHECK.guide_name(other, locale) for other in CHECK.GUIDES if other != stem
                ]
                navigation = " · ".join(f"[{target}]({target})" for target in destinations)
                text = f"# {stem}\n\n{navigation}\n\n## Details\n\nContent.\n"
                (self.root / name).write_text(text, encoding="utf-8")
        for stem in CHECK.GUIDES:
            source = self.root / f"{stem}.md"
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            for locale in CHECK.LOCALES[1:]:
                path = self.root / CHECK.guide_name(stem, locale)
                marker = (
                    f"<!-- translation-source: {source.name}; source-sha256: {digest}; "
                    "status: translated -->\n"
                )
                path.write_text(marker + path.read_text(encoding="utf-8"), encoding="utf-8")

    def append(self, name, text):
        path = self.root / name
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def errors(self):
        return "\n".join(CHECK.check_documents(self.root))

    def test_repository_guides_and_distribution_manifest(self):
        self.assertEqual(CHECK.check_documents(ROOT), [])
        self.assertEqual(CHECK.check_packaging(ROOT), [])

    def test_source_change_marks_each_translation_stale_without_rewriting(self):
        self.assertEqual(self.errors(), "")
        before = (self.root / "UPDATES.ja.md").read_bytes()
        self.append("UPDATES.md", "\nA new recovery restriction.\n")
        errors = self.errors()
        for locale in CHECK.LOCALES[1:]:
            self.assertIn(f"UPDATES.{locale}.md: stale translation", errors)
        self.assertEqual((self.root / "UPDATES.ja.md").read_bytes(), before)
        self.assertNotIn("README.ko.md: stale", errors)

    def test_missing_translation_and_marker_are_reported(self):
        (self.root / "UPDATES.zh-CN.md").unlink()
        path = self.root / "README.ko.md"
        path.write_text(path.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        errors = self.errors()
        self.assertIn("UPDATES.zh-CN.md: missing guide", errors)
        self.assertIn("README.ko.md: missing or invalid translation-source marker", errors)
        self.assertIn("missing relative target UPDATES.zh-CN.md", errors)

    def test_missing_language_and_same_language_navigation_are_reported(self):
        path = self.root / "README.ko.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("[README.ja.md](README.ja.md)", "")
        text = text.replace("[UPDATES.ko.md](UPDATES.ko.md)", "[Updates](UPDATES.md)")
        path.write_text(text, encoding="utf-8")
        errors = self.errors()
        self.assertIn("missing navigation link to README.ja.md", errors)
        self.assertIn("missing navigation link to UPDATES.ko.md", errors)
        self.assertIn("cross-language guide link to UPDATES.md", errors)

    def test_relative_fragments_reference_links_and_explicit_anchors(self):
        (self.root / "examples").mkdir()
        (self.root / "examples/guide.md").write_text(
            '# Example\n\n## 日本語の手順\n\n<a id="stable-id"></a>\n'
            "\n## Duplicate\n\n## Duplicate\n",
            encoding="utf-8",
        )
        self.append(
            "README.ja.md",
            "\n[Section](examples/guide.md#日本語の手順)\n"
            "[Stable][reference]\n\n[reference]: examples/guide.md#stable-id\n"
            "[Second](examples/guide.md#duplicate-1)\n"
            "[Local](#details)\n"
            "[Remote](https://example.invalid/absent#remote)\n"
            "```md\n[Not a link](absent.md#missing)\n```\n",
        )
        self.assertEqual(self.errors(), "")
        self.append("README.ja.md", "\n[Broken](examples/guide.md#missing)\n")
        self.assertIn("missing relative anchor examples/guide.md#missing", self.errors())
        self.append("README.ja.md", "\n[Missing](examples/absent.md)\n")
        self.assertIn("missing relative target examples/absent.md", self.errors())

    def test_html_links_and_percent_encoded_fragments(self):
        (self.root / "example.html").write_text('<h2 id="日本語">Title</h2>', encoding="utf-8")
        self.append(
            "DEMO.ja.md",
            '\n<a href="example.html#%E6%97%A5%E6%9C%AC%E8%AA%9E">Example</a>\n',
        )
        self.assertEqual(self.errors(), "")
        self.append("DEMO.ja.md", '\n<a href="example.html#absent">Missing</a>\n')
        self.assertIn("missing relative anchor example.html#absent", self.errors())

    def test_distribution_manifest_reports_omitted_translation(self):
        names = sorted(CHECK.guide_names() - {"OPERATIONS.ja.md"})
        values = ", ".join(f'"{name}"' for name in names)
        (self.root / "pyproject.toml").write_text(
            f"[tool.hatch.build.targets.sdist]\nonly-include = [{values}]\n",
            encoding="utf-8",
        )
        self.assertEqual(
            CHECK.check_packaging(self.root), ["sdist configuration: missing OPERATIONS.ja.md"]
        )

    def make_archive(self, missing=None, changed=None):
        archive = self.root / "package.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            for name in sorted(CHECK.guide_names()):
                if name == missing:
                    continue
                content = (self.root / name).read_bytes()
                if name == changed:
                    content += b"\nOld packaged content.\n"
                member = tarfile.TarInfo(f"todo_flow-0.0.9/{name}")
                member.size = len(content)
                package.addfile(member, io.BytesIO(content))
        return archive

    def test_actual_archive_contents_must_include_current_translations(self):
        self.assertEqual(CHECK.check_sdist(self.make_archive(), self.root), [])
        self.assertEqual(
            CHECK.check_sdist(self.make_archive(missing="UPDATES.ko.md"), self.root),
            ["sdist: missing UPDATES.ko.md"],
        )
        self.assertEqual(
            CHECK.check_sdist(self.make_archive(changed="DEMO.zh-CN.md"), self.root),
            ["sdist: content differs from workspace: DEMO.zh-CN.md"],
        )
