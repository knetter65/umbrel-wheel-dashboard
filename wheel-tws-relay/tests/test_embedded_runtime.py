from __future__ import annotations

import base64
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def embedded_content(variable: str) -> bytes:
    match = re.search(rf'{re.escape(variable)}: "([A-Za-z0-9+/=]+)"', COMPOSE)
    if match is None:
        raise AssertionError(f"missing embedded runtime: {variable}")
    return base64.b64decode(match.group(1), validate=True)


class EmbeddedRuntimeTests(unittest.TestCase):
    def test_relay_runtime_matches_tested_source(self) -> None:
        self.assertEqual(
            embedded_content("TWS_RELAY_CODE_B64"),
            (ROOT / "app" / "tws_relay.py").read_bytes(),
        )

    def test_status_runtime_matches_tested_source(self) -> None:
        self.assertEqual(
            embedded_content("TWS_STATUS_CODE_B64"),
            (ROOT / "app" / "status_server.py").read_bytes(),
        )

    def test_persistent_app_source_is_not_mounted(self) -> None:
        self.assertNotIn("${APP_DATA_DIR}/app", COMPOSE)
        self.assertNotIn("configs:", COMPOSE)
        self.assertIn("/tmp/tws_relay.py", COMPOSE)
        self.assertIn("/tmp/status_server.py", COMPOSE)


if __name__ == "__main__":
    unittest.main()
