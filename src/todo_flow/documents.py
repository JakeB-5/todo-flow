"""Agent-readable Markdown documents with JSON frontmatter and normal prose sections."""

import base64
import html
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from markdown_it import MarkdownIt


import json
import re

SECTIONS = {
    "goal": "Goal",
    "scope": "Scope",
    "evidence": "Problem and evidence",
    "design": "Approach and decisions",
}
KOREAN_SECTIONS = {
    "goal": "목표",
    "scope": "범위",
    "evidence": "문제와 근거",
    "design": "접근과 결정",
}


def render(doc):
    metadata = {k: v for k, v in doc.items() if k not in SECTIONS}
    header = json.dumps(metadata, ensure_ascii=False, indent=2)
    parts = ["---\n" + header + "\n---\n"]
    for key, title in SECTIONS.items():
        if doc.get("language") == "ko":
            title = KOREAN_SECTIONS[key]
        if key in doc:
            # JSON values other than prose remain supported by legacy documents.
            value = doc[key]
            if not isinstance(value, str):
                raise ValueError(f"{key} must be text")
            if re.search(r"^<!-- /?todo-flow:", value, re.M):
                raise ValueError("Reserved document delimiter in prose")
            parts.append(
                f"## {title}\n<!-- todo-flow:{key} -->\n{value}\n<!-- /todo-flow:{key} -->\n"
            )
    return "\n".join(parts)


def parse(text):
    if not text.startswith("---\n"):
        raise ValueError("Track Markdown requires JSON frontmatter")
    metadata, body = text[4:].split("\n---\n", 1)
    doc = json.loads(metadata)
    for key in SECTIONS:
        match = re.search(
            rf"<!-- todo-flow:{key} -->\n(.*?)\n<!-- /todo-flow:{key} -->", body, re.S
        )
        if match:
            doc[key] = match[1]
    return doc


# Rich documents are preserved as authored; the structured block is only the runtime contract.


class ContractParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inside = False
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "todo-flow-track":
            if attrs.get("type") != "application/json":
                raise ValueError("todo-flow-track must be application/json")
            self.inside = True
            self.blocks.append("")

    def handle_data(self, data):
        if self.inside:
            self.blocks[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False


def decode_source(text, mode="record"):
    if mode == "html":
        parser = ContractParser()
        parser.feed(text)
        if len(parser.blocks) != 1:
            raise ValueError(
                'HTML requires one <script type="application/json" id="todo-flow-track"> contract'
            )
        doc = json.loads(parser.blocks[0])
    else:
        doc = parse(text)
    if "presentation" in doc:
        raise ValueError("presentation is managed by the registration loader")
    if mode != "record":
        doc["presentation"] = {"format": mode, "source": text, "assets": []}
    return doc


def asset_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("Asset must be a relative path inside assets/")
    return path.as_posix()


def load(file, assets=None):
    file = Path(file)
    if file.suffix.lower() == ".json":
        doc = json.loads(file.read_text())
        if assets:
            raise ValueError("Assets require an HTML or Markdown source document")
        return doc
    mode = "html" if file.suffix.lower() in (".html", ".htm") else "markdown"
    doc = decode_source(file.read_text(), mode)
    if assets:
        root = Path(assets).resolve()
        if not root.is_dir():
            raise ValueError("Asset directory does not exist")
        for asset in sorted(root.rglob("*")):
            if asset.is_symlink():
                raise ValueError("Asset symlinks are not supported; copy the intended file")
            if asset.is_file():
                doc["presentation"]["assets"].append(
                    {
                        "path": asset_name(asset.relative_to(root).as_posix()),
                        "data": base64.b64encode(asset.read_bytes()).decode("ascii"),
                    }
                )
    return doc


def source(doc):
    presentation = doc.get("presentation")
    return presentation["source"] if presentation else render(doc)


def mode(doc):
    return doc.get("presentation", {}).get("format", "record")


def validate_presentation(doc):
    if "presentation" not in doc:
        return
    p = doc["presentation"]
    if p.get("format") not in ("html", "markdown"):
        raise ValueError("Unsupported document format")
    decoded = decode_source(p["source"], p["format"])
    plain = {k: v for k, v in doc.items() if k != "presentation"}
    if {k: v for k, v in decoded.items() if k != "presentation"} != plain:
        raise ValueError("Document contract and authored source disagree")
    seen = set()
    for asset in p.get("assets", []):
        name = asset_name(asset["path"])
        if name in seen:
            raise ValueError("Duplicate asset path")
        seen.add(name)
        base64.b64decode(asset["data"], validate=True)


def assets_for(doc):
    return {
        asset_name(a["path"]): base64.b64decode(a["data"], validate=True)
        for a in doc.get("presentation", {}).get("assets", [])
    }


def render_html(doc):
    if mode(doc) == "html":
        return source(doc)
    language = "ko" if doc.get("language") == "ko" else "en"
    heading, result_label, method_label = (
        ("완료 조건", "확인할 결과", "검증 방법")
        if language == "ko"
        else ("Acceptance conditions", "Expected result", "Verification method")
    )
    text = source(doc).split("\n---\n", 1)[1]
    body = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).render(text)
    conditions = "".join(
        f"<tr><td>{html.escape(c['id'])}</td><td>{html.escape(c['text'])}</td><td>{html.escape(c['method'])}</td></tr>"
        for c in doc.get("conditions", [])
    )
    contract = json.dumps(
        {k: v for k, v in doc.items() if k != "presentation"}, ensure_ascii=False, indent=2
    ).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(doc["title"])}</title><style>
body{{max-width:1100px;margin:32px auto;padding:0 24px;color:#243a31;background:#f8faf7;font:15px/1.8 system-ui,sans-serif}}h1{{font-size:32px}}h2{{margin-top:32px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9e1da;padding:10px;text-align:left}}img,svg,canvas,video{{max-width:100%}}pre{{overflow:auto;padding:16px;background:#edf2ec}}figure{{margin:24px 0}}figcaption{{color:#627567;font-size:13px}}code{{overflow-wrap:anywhere}}
</style><script type="application/json" id="todo-flow-track">{contract}</script></head><body><header><small>{html.escape(doc["id"])}</small><h1>{html.escape(doc["title"])}</h1></header>{body}<section><h2>{heading}</h2><table><thead><tr><th>ID</th><th>{result_label}</th><th>{method_label}</th></tr></thead><tbody>{conditions}</tbody></table></section></body></html>"""
