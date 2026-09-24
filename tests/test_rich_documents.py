import json
import shutil
import tempfile
import unittest
from pathlib import Path

from todo_flow import documents
from todo_flow.projections import Dashboard
from todo_flow.store import Store

DOC = {
    "id": "rich-track",
    "title": "시각 검토",
    "goal": "회전축 유지",
    "scope": "패널",
    "evidence": "도해와 실험",
    "conditions": [{"id": "hinge", "text": "모서리 유지", "method": "각도 조작"}],
}


def html_source(doc, text="diagram"):
    return (
        '<!doctype html><html><script type="application/json" id="todo-flow-track">'
        + json.dumps(doc, ensure_ascii=False)
        + "</script><body><svg><text>"
        + text
        + '</text></svg><img src="assets/image.png"><script type="module" src="assets/view.js"></script></body></html>'
    )


class RichDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "todo")
        self.store.configure({"github": None, "base": "main", "endpoint": "review"})
        self.asset = self.root / "bundle"
        self.asset.mkdir()
        (self.asset / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
        (self.asset / "view.js").write_text('document.body.dataset.ready="true";')

    def tearDown(self):
        self.tmp.cleanup()

    def test_html_and_binary_assets_are_exact_and_revisioned(self):
        path = self.root / "input.html"
        original = html_source(DOC)
        path.write_text(original)
        doc = documents.load(path, self.asset)
        self.store.register(doc)
        track = self.store.path / "tracks/rich-track"
        self.assertEqual((track / "track.html").read_text(), original)
        self.assertEqual(
            (track / "assets/image.png").read_bytes(), (self.asset / "image.png").read_bytes()
        )
        path.write_text(html_source(DOC, "new visual"))
        (self.asset / "view.js").write_text('document.body.dataset.ready="new";')
        self.store.register(documents.load(path, self.asset), expected=1)
        shutil.rmtree(self.store.path / ".cache")
        restored = Store(self.store.path)
        self.assertEqual(
            json.loads(restored.track("rich-track")["document"])["presentation"]["source"],
            path.read_text(),
        )
        with restored.connect() as c:
            old = json.loads(c.execute("SELECT body FROM documents WHERE revision=1").fetchone()[0])
        self.assertEqual(documents.render_html(old), original)
        self.assertIn(b'"true"', documents.assets_for(old)["view.js"])
        detail = Dashboard(restored).detail("rich-track")
        self.assertNotIn("presentation", detail["document"])
        self.assertEqual(detail["documentView"]["url"], "/documents/rich-track/2/index.html")

    def test_markdown_preserves_arbitrary_body_and_renders_table_svg_script(self):
        text = (
            "---\n"
            + json.dumps(DOC, ensure_ascii=False)
            + '\n---\n# 사람용 계획\n\n|조건|결과|\n|-|-|\n|A|B|\n\n<svg id="drawing"></svg>\n\n<script>window.example=1;</script>\n'
        )
        path = self.root / "input.md"
        path.write_text(text)
        doc = documents.load(path)
        self.store.register(doc)
        rendered = (self.store.path / "tracks/rich-track/track.html").read_text()
        self.assertIn("<table>", rendered)
        self.assertIn('<svg id="drawing">', rendered)
        self.assertIn("window.example=1", rendered)
        self.assertEqual((self.store.path / "tracks/rich-track/source.md").read_text(), text)
        shutil.rmtree(self.store.path / ".cache")
        reread = json.loads(Store(self.store.path).track("rich-track")["document"])
        self.assertEqual(doc, reread)

    def test_source_contract_drift_and_unsafe_asset_paths_rejected(self):
        path = self.root / "input.html"
        path.write_text(html_source(DOC))
        doc = documents.load(path)
        doc["goal"] = "unrelated"
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.store.register(doc)
        with self.assertRaises(ValueError):
            documents.asset_name("../outside")
        (self.asset / "linked").symlink_to(path)
        with self.assertRaisesRegex(ValueError, "symlink"):
            documents.load(path, self.asset)
