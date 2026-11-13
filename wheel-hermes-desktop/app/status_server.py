#!/usr/bin/env python3
"""Minimal authenticated status UI for the Hermes Desktop companion."""
from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATUS_PORT = 8080
PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Hermes Desktop companion</title></head>
<body>
<h1>Hermes Desktop companion</h1>
<p>This app provides a private <code>hermes serve</code> backend. Configure listen host and port in Umbrel app settings. Do not expose a public port.</p>
<p>Status: package installed. Serve health is the container healthcheck after settings are applied.</p>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesDesktopStatus/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            return
        if self.path == "/healthz":
            self.send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")
            return
        self.send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
