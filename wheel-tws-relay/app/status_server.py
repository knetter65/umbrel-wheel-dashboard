#!/usr/bin/env python3
"""Minimal authenticated status UI for the private TWS relay."""
from __future__ import annotations

import hmac
import html
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ALLOWED_FIELDS = {
    "status",
    "updated_at",
    "active_connections",
    "accepted_total",
    "rejected_total",
    "upstream_failures",
    "payload_logging",
    "error",
}
_STATE: dict[str, object] = {"status": "waiting_for_relay"}
_STATE_LOCK = threading.Lock()


def load_state() -> dict[str, object]:
    with _STATE_LOCK:
        return dict(_STATE)


def store_state(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("state is not an object")
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError("state contains unknown fields")
    with _STATE_LOCK:
        _STATE.clear()
        _STATE.update(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "TWSRelayStatus/2"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/internal/status":
            self.send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
            return
        expected = os.environ.get("TWS_RELAY_STATUS_TOKEN", "")
        supplied = self.headers.get("X-Relay-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            self.send_bytes(HTTPStatus.FORBIDDEN, "text/plain; charset=utf-8", b"forbidden\n")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 16384:
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(size))
            store_state(payload)
        except (ValueError, json.JSONDecodeError):
            self.send_bytes(HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", b"bad request\n")
            return
        self.send_bytes(HTTPStatus.NO_CONTENT, "text/plain; charset=utf-8", b"")

    def do_GET(self) -> None:  # noqa: N802
        state = load_state()
        if self.path == "/healthz":
            self.send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")
            return
        if self.path == "/api/status":
            body = (json.dumps(state, sort_keys=True) + "\n").encode()
            self.send_bytes(HTTPStatus.OK, "application/json", body)
            return
        if self.path not in {"/", "/index.html"}:
            self.send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
            return
        rows = "".join(
            f"<dt>{html.escape(str(key))}</dt><dd>{html.escape(str(value))}</dd>"
            for key, value in sorted(state.items())
        )
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Private TWS Relay</title><style>
body{{font:16px system-ui;max-width:760px;margin:3rem auto;padding:0 1rem;background:#111;color:#eee}}
code,dd{{color:#9fe3b1}}dl{{display:grid;grid-template-columns:max-content 1fr;gap:.7rem 1rem}}
dt{{font-weight:700}}dd{{margin:0;overflow-wrap:anywhere}}.note{{color:#bbb}}
</style></head><body><h1>Private TWS Relay</h1>
<p class="note">Payload-blind, private-network relay. No broker payloads are logged.</p><dl>{rows}</dl></body></html>""".encode()
        self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)


def main() -> None:
    port = int(os.environ.get("STATUS_PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
