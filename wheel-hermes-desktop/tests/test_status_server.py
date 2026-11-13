from __future__ import annotations

import importlib.util
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "app" / "status_server.py"
SPEC = importlib.util.spec_from_file_location("desktop_status_server", MODULE_PATH)
assert SPEC and SPEC.loader
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


class StatusServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = status.ThreadingHTTPServer(("127.0.0.1", 0), status.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def test_healthz(self) -> None:
        body = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=2).read()
        self.assertEqual(body, b"ok\n")

    def test_index_has_no_listen_endpoint(self) -> None:
        body = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=2).read().decode()
        self.assertIn("hermes serve", body)
        self.assertNotIn("fd7a:", body)
        self.assertNotIn("10.21.0.1", body)

    def test_unknown_path(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/secret", timeout=2)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
