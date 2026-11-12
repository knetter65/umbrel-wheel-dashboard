#!/usr/bin/env python3
"""Minimal read-only status UI for the private TWS relay."""
from __future__ import annotations

import html
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE_PATH = Path(os.environ.get("TWS_RELAY_STATE_PATH", "/state/relay-status.json"))


def load_state() -> dict[str, object]:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("state is not an object")
        return payload
    except FileNotFoundError:
        return {"status": "waiting_for_relay"}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "invalid_state"}


class Handler(BaseHTTPRequestHandler):
    server_version = "TWSRelayStatus/1"

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
