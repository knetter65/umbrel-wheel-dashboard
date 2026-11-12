from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "app" / "status_server.py"
SPEC = importlib.util.spec_from_file_location("status_server", MODULE_PATH)
assert SPEC and SPEC.loader
status_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status_module)


class StatusTests(unittest.TestCase):
    def test_missing_state_is_reported_without_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = status_module.STATE_PATH
            status_module.STATE_PATH = Path(directory) / "missing.json"
            try:
                self.assertEqual(status_module.load_state(), {"status": "waiting_for_relay"})
            finally:
                status_module.STATE_PATH = original

    def test_valid_state_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"status": "ready", "payload_logging": False}))
            original = status_module.STATE_PATH
            status_module.STATE_PATH = path
            try:
                state = status_module.load_state()
                self.assertEqual(state["status"], "ready")
                self.assertFalse(state["payload_logging"])
            finally:
                status_module.STATE_PATH = original

    def test_http_health_and_status_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"status": "ready", "payload_logging": False}))
            original = status_module.STATE_PATH
            status_module.STATE_PATH = path
            server = ThreadingHTTPServer(("127.0.0.1", 0), status_module.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    self.assertEqual(response.read(), b"ok\n")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["status"], "ready")
                self.assertNotIn("host", payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                status_module.STATE_PATH = original


if __name__ == "__main__":
    unittest.main()
