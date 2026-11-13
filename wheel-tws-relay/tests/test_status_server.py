from __future__ import annotations

import importlib.util
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "app" / "status_server_v012.py"
SPEC = importlib.util.spec_from_file_location("status_server", MODULE_PATH)
assert SPEC and SPEC.loader
status_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status_module)


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        status_module.store_state({"status": "waiting_for_relay"})

    def test_state_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            status_module.store_state({"status": "ready", "host": "not-allowed"})

    def test_valid_state_is_loaded(self) -> None:
        status_module.store_state({"status": "ready", "payload_logging": False})
        state = status_module.load_state()
        self.assertEqual(state["status"], "ready")
        self.assertFalse(state["payload_logging"])

    def test_http_health_and_authenticated_status_update(self) -> None:
        with patch.dict(os.environ, {"TWS_RELAY_STATUS_TOKEN": "test-token"}, clear=False):
            server = ThreadingHTTPServer(("127.0.0.1", 0), status_module.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    self.assertEqual(response.read(), b"ok\n")

                body = json.dumps({"status": "ready", "payload_logging": False}).encode()
                unauthorized = urllib.request.Request(
                    f"http://127.0.0.1:{port}/internal/status",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(unauthorized, timeout=2)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()

                authorized = urllib.request.Request(
                    f"http://127.0.0.1:{port}/internal/status",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Relay-Token": "test-token",
                    },
                )
                with urllib.request.urlopen(authorized, timeout=2) as response:
                    self.assertEqual(response.status, 204)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["status"], "ready")
                self.assertNotIn("host", payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
