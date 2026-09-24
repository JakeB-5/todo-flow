"""Loopback-only projection; no track registration or document editing endpoint."""

import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import documents
from .store import Conflict, encode
from .projections import Dashboard


def serve(store, port=8765):
    token = secrets.token_urlsafe(32)
    dashboard = Dashboard(store)
    assets = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def reply(self, status, value, content_type="application/json", headers=None):
            body = (
                value
                if isinstance(value, bytes)
                else value.encode()
                if isinstance(value, str)
                else encode(value).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in hosts

        def do_GET(self):
            if not self.trusted():
                self.reply(403, {"error": "Invalid host"})
                return
            url = urlparse(self.path)
            path = url.path
            params = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if path.startswith("/documents/"):
                try:
                    parts = path.split("/")
                    if len(parts) < 5:
                        raise ValueError("Invalid document path")
                    id_, revision = unquote(parts[2]), int(parts[3])
                    with store.connect() as c:
                        record = c.execute(
                            "SELECT body FROM documents WHERE track=? AND revision=?",
                            (id_, revision),
                        ).fetchone()
                        if record:
                            doc = json.loads(record["body"])
                        else:
                            # Legacy fixture rows may not have a separate document revision.
                            t = store.track(id_, c)
                            if t["revision"] != revision:
                                raise ValueError("Unknown document revision")
                            doc = json.loads(t["document"])
                    if parts[4:] == ["index.html"]:
                        self.reply(
                            200,
                            documents.render_html(doc),
                            "text/html",
                            {
                                "Content-Security-Policy": "sandbox allow-scripts allow-downloads",
                                "Referrer-Policy": "no-referrer",
                            },
                        )
                    elif parts[4] == "assets":
                        name = documents.asset_name(unquote("/".join(parts[5:])))
                        data = documents.assets_for(doc).get(name)
                        if data is None:
                            raise ValueError("Unknown document asset")
                        self.reply(
                            200,
                            data,
                            mimetypes.guess_type(name)[0] or "application/octet-stream",
                            {
                                "Access-Control-Allow-Origin": "*",
                                "Content-Security-Policy": "sandbox allow-scripts",
                                "Referrer-Policy": "no-referrer",
                            },
                        )
                    else:
                        raise ValueError("Unknown document resource")
                except (ValueError, TypeError, KeyError):
                    self.reply(404, {"error": "Document or asset not found"})
            elif path.startswith("/api/"):
                try:
                    if path in ("/api/overview", "/api/state"):
                        result = dashboard.overview()
                        result["token"] = token
                        if path == "/api/state":
                            result["tracks"] = dashboard.tracks()["items"]
                            result["bounded"] = True
                    elif path == "/api/tracks":
                        result = dashboard.tracks(**params)
                    elif path.startswith("/api/tracks/"):
                        pieces = path.split("/")
                        id_ = unquote(pieces[3])
                        result = (
                            dashboard.evidence(id_, pieces[5])
                            if len(pieces) == 6 and pieces[4] == "evidence"
                            else dashboard.detail(id_)
                        )
                    elif path == "/api/activity":
                        result = dashboard.activity(**params)
                    elif path == "/api/decisions":
                        result = dashboard.decisions(**params)
                    elif path == "/api/events":
                        result = dashboard.events(**params)
                    elif path.startswith("/api/tasks/"):
                        result = dashboard.task(unquote(path.split("/")[-1]))
                    elif path == "/api/watches":
                        result = dashboard.watches(**params)
                    else:
                        self.reply(404, {"error": "Not found"})
                        return
                    self.reply(200, result)
                except (ValueError, TypeError) as e:
                    self.reply(400, {"error": str(e)})
            elif path in ("/", "/app.js", "/style.css"):
                file, mime = {
                    "/": ("index.html", "text/html"),
                    "/app.js": ("app.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css"),
                }[path]
                self.reply(200, (assets / file).read_text(), mime)
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.trusted() or self.headers.get("X-Todo-Flow") != token:
                self.reply(403, {"error": "Local session token required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                self.reply(403, {"error": "Cross-origin request refused"})
                return
            if store.config().get("demo"):
                self.reply(
                    409,
                    {
                        "error": "규모 검증용 샘플은 읽기 전용입니다. 실제 실행은 세션에서 trackrun으로 요청하세요."
                    },
                )
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n < 1 or n > 100_000:
                    raise ValueError("Invalid request length")
                data = json.loads(self.rfile.read(n))
                path = urlparse(self.path).path
                if path == "/api/control":
                    out = {"control": store.control(data["track"], data["action"])}
                elif path == "/api/answer":
                    store.answer(data["decision"], data["answer"])
                    out = {"answered": data["decision"]}
                else:
                    self.reply(404, {"error": "No such command; authoring is agent-only"})
                    return
                self.reply(200, out)
            except (ValueError, KeyError, Conflict) as e:
                self.reply(409, {"error": str(e)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Dashboard: http://127.0.0.1:" + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
