"""Modo navegador: sirve la interfaz en http://127.0.0.1 y expone la API por
HTTP. Se usa si no hay ventana nativa disponible y para las pruebas.

Solo escucha en el propio equipo y exige un token aleatorio en cada llamada,
así ninguna web externa puede usar la API."""

from __future__ import annotations

import json
import mimetypes
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .api import Api, Bridge

UI_DIR = Path(__file__).resolve().parent / "ui"


def make_handler(bridge: Bridge, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._host_ok():
                self._send(403, b"", "text/plain")
                return
            path = urlparse(self.path).path
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            file = (UI_DIR / name).resolve()
            if UI_DIR not in file.parents or not file.is_file():
                self._send(404, b"No encontrado", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, file.read_bytes(), ctype)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._host_ok() or not path.startswith("/api/") or self.headers.get("X-TDL-Token") != token:
                self._send(403, b"{}", "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            result = bridge.call(path[len("/api/"):], body.get("args") or [])
            self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(api: Api, *, port: int = 0, open_browser: bool = True) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Bridge(api), token))
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"
    if open_browser:
        webbrowser.open(url)
    return server, url
