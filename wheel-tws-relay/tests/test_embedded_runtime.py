from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def embedded_content(name: str) -> str:
    marker = f"  {name}:\n    content: |\n"
    if marker not in COMPOSE:
        raise AssertionError(f"missing embedded config: {name}")
    body = COMPOSE.split(marker, 1)[1]
    if name == "relay_runtime":
        body = body.split("\n  status_runtime:\n", 1)[0]
    lines = body.rstrip("\n").splitlines()
    return "\n".join(line[6:] if line else "" for line in lines) + "\n"


class EmbeddedRuntimeTests(unittest.TestCase):
    def test_relay_runtime_matches_tested_source(self) -> None:
        self.assertEqual(
            embedded_content("relay_runtime"),
            (ROOT / "app" / "tws_relay.py").read_text(),
        )

    def test_status_runtime_matches_tested_source(self) -> None:
        self.assertEqual(
            embedded_content("status_runtime"),
            (ROOT / "app" / "status_server.py").read_text(),
        )

    def test_persistent_app_source_is_not_mounted(self) -> None:
        self.assertNotIn("${APP_DATA_DIR}/app", COMPOSE)
        self.assertIn("target: /runtime/tws_relay.py", COMPOSE)
        self.assertIn("target: /runtime/status_server.py", COMPOSE)


if __name__ == "__main__":
    unittest.main()
