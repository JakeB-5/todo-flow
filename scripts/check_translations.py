"""Check core guide coverage, source hashes, local links and sdist contents offline."""

import argparse
import hashlib
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import unicodedata
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

LOCALES = ("en", "ko", "ja", "zh-CN")
GUIDES = ("README", "AGENT_INSTALL", "OPERATIONS", "UPDATES", "DEMO")
MARKER = re.compile(
    r"<!-- translation-source: ([^;]+); source-sha256: ([0-9a-f]{64}); "
    r"status: translated -->"
)


def guide_name(stem, locale):
    return f"{stem}.md" if locale == "en" else f"{stem}.{locale}.md"


def guide_names():
    return {guide_name(stem, locale) for stem in GUIDES for locale in LOCALES}


class HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.anchors.add(attributes["id"])
        if tag == "a" and attributes.get("name"):
            self.anchors.add(attributes["name"])
        for key in ("href", "src"):
            if attributes.get(key):
                self.links.append(attributes[key])


def walk_tokens(tokens):
    for token in tokens:
        yield token
        yield from walk_tokens(token.children or [])


def heading_slug(text):
    # GitHub-style IDs for the plain-text headings used in these guides.
    text = text.lower()
    text = "".join(
        character
        for character in text
        if character in "-_" or unicodedata.category(character)[0] not in "PS"
    )
    return re.sub(r"\s", "-", text)


def references(text):
    tokens = MarkdownIt("commonmark").parse(text)
    html = HTMLReferences()
    links = []
    anchors = set()
    used = set()
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            plain = "".join(
                child.content
                for child in inline.children or []
                if child.type in ("text", "code_inline", "image")
            )
            base = heading_slug(plain)
            slug = base
            suffix = 0
            while slug in used:
                suffix += 1
                slug = f"{base}-{suffix}"
            used.add(slug)
            anchors.add(slug)
    for token in walk_tokens(tokens):
        if token.type in ("html_block", "html_inline"):
            html.feed(token.content)
        if token.type == "link_open":
            links.append(token.attrGet("href"))
        if token.type == "image":
            links.append(token.attrGet("src"))
    html.close()
    return links + html.links, anchors | html.anchors


def check_documents(root):
    root = Path(root).resolve()
    errors = []
    parsed = {}
    names = guide_names()
    for stem in GUIDES:
        source = root / guide_name(stem, "en")
        digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
        for locale in LOCALES:
            name = guide_name(stem, locale)
            path = root / name
            if not path.is_file():
                errors.append(f"{name}: missing guide")
                continue
            text = path.read_text(encoding="utf-8")
            if locale != "en":
                markers = MARKER.findall(text)
                if len(markers) != 1 or markers[0][0] != source.name:
                    errors.append(f"{name}: missing or invalid translation-source marker")
                elif markers[0][1] != digest:
                    errors.append(f"{name}: stale translation of {source.name}")
            links, anchors = references(text)
            parsed[path] = (links, anchors)
            destinations = {
                unquote(urlsplit(link).path)
                for link in links
                if not urlsplit(link).scheme and not urlsplit(link).netloc
            }
            required = {guide_name(stem, language) for language in LOCALES}
            required.update(guide_name(other, locale) for other in GUIDES if other != stem)
            for missing in sorted(required - destinations):
                errors.append(f"{name}: missing navigation link to {missing}")
            siblings = {guide_name(stem, language) for language in LOCALES}
            same_language = {guide_name(other, locale) for other in GUIDES}
            for destination in sorted(destinations & names - siblings - same_language):
                errors.append(f"{name}: cross-language guide link to {destination}")
    for path, (links, _) in list(parsed.items()):
        for link in links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not target.exists():
                errors.append(f"{path.name}: missing relative target {link}")
                continue
            if not url.fragment:
                continue
            if not target.is_file():
                errors.append(f"{path.name}: fragment target is not a file: {link}")
                continue
            if target.suffix.lower() not in (".md", ".html", ".svg"):
                continue
            if target not in parsed:
                text = target.read_text(encoding="utf-8")
                if target.suffix.lower() == ".md":
                    parsed[target] = references(text)
                else:
                    html = HTMLReferences()
                    html.feed(text)
                    html.close()
                    parsed[target] = (html.links, html.anchors)
            if unquote(url.fragment) not in parsed[target][1]:
                errors.append(f"{path.name}: missing relative anchor {link}")
    return errors


def check_packaging(root):
    configuration = tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))
    included = set(configuration["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"])
    return [f"sdist configuration: missing {name}" for name in sorted(guide_names() - included)]


def check_sdist(archive, root):
    errors = []
    with tarfile.open(archive, "r:gz") as package:
        members = {}
        for member in package.getmembers():
            parts = PurePosixPath(member.name).parts
            if member.isfile() and len(parts) == 2:
                members[parts[1]] = member
        for name in sorted(guide_names()):
            if name not in members:
                errors.append(f"sdist: missing {name}")
                continue
            with package.extractfile(members[name]) as stream:
                content = stream.read()
            source = Path(root) / name
            if not source.is_file() or content != source.read_bytes():
                errors.append(f"sdist: content differs from workspace: {name}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--sdist", type=Path, help="Built .tar.gz to inspect without extracting")
    args = parser.parse_args()
    errors = check_documents(args.root) + check_packaging(args.root)
    if args.sdist:
        errors.extend(check_sdist(args.sdist, args.root))
    for error in errors:
        print(error)
    if not errors:
        print("Core guide translations, source hashes and local links are current.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
