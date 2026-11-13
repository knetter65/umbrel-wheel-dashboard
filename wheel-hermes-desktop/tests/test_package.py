from __future__ import annotations

import base64
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()
MANIFEST = (ROOT / "umbrel-app.yml").read_text()


class PackageContractTests(unittest.TestCase):
    def test_app_id_and_version(self) -> None:
        self.assertIn("\nid: wheel-hermes-desktop\n", MANIFEST)
        self.assertIn('version: "0.1.0"', MANIFEST)

    def test_serve_uses_pinned_hermes_image(self) -> None:
        self.assertIn(
            "ghcr.io/getumbrel/hermes-agent-umbrel:v0.23.1@sha256:b7976dc05ed1348011c82bff15f04064eee53e307839337c9eec0b47af79b4f1",
            COMPOSE,
        )

    def test_serve_is_host_network_and_hardened(self) -> None:
        self.assertIn("network_mode: host", COMPOSE)
        self.assertIn("no-new-privileges:true", COMPOSE)
        self.assertIn("cap_drop:", COMPOSE)
        self.assertIn('user: "1000:1000"', COMPOSE)
        self.assertIn("create_host_path: false", COMPOSE)

    def test_narrow_vault_mount_only(self) -> None:
        self.assertIn("/obsidian/Obsidian Vault/Hermes Vault", COMPOSE)
        self.assertNotIn("target: /obsidian/Obsidian Vault\n", COMPOSE)
        self.assertIn("/home/umbrel/umbrel/app-data/hermes-agent/data/hermes", COMPOSE)

    def test_encoded_bootstrap_matches_source(self) -> None:
        match = re.search(r'HERMES_DESKTOP_BOOTSTRAP_B64: "([^"]+)"', COMPOSE)
        self.assertIsNotNone(match)
        decoded = base64.b64decode(match.group(1))
        self.assertEqual(decoded, (ROOT / "app" / "serve_bootstrap.py").read_bytes())
        self.assertIn(b"serve", decoded)
        self.assertNotIn(b"gateway run", decoded)

    def test_encoded_status_matches_source(self) -> None:
        match = re.search(r'HERMES_DESKTOP_STATUS_B64: "([^"]+)"', COMPOSE)
        self.assertIsNotNone(match)
        decoded = base64.b64decode(match.group(1))
        self.assertEqual(decoded, (ROOT / "app" / "status_server.py").read_bytes())

    def test_no_private_identifiers_in_package(self) -> None:
        blob = "\n".join(
            path.read_text()
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".yml", ".md", ".yaml"}
            and "tests" not in path.parts
        )
        forbidden = [
            r"fd7a:",
            r"\b100\.\d+\.\d+\.\d+\b",
            r"BEGIN OPENSSH",
            r"hermes-tws-tailscale-relay",
            r"TWS_RELAY_",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, blob), pattern)

    def test_listen_settings_are_not_hardcoded(self) -> None:
        self.assertNotIn("HERMES_DESKTOP_LISTEN_HOST:", COMPOSE)
        self.assertNotIn("HERMES_DESKTOP_LISTEN_PORT:", COMPOSE)


class ComposeConfigTests(unittest.TestCase):
    def test_docker_compose_config_with_dummy_proxy(self) -> None:
        import shutil
        import subprocess
        import tempfile

        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("docker CLI not available")
        probe = subprocess.run([docker, "compose", "version"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("docker compose plugin not available")
        env = os.environ.copy()
        env["APP_SEED"] = "validation-only"
        env["APP_DATA_DIR"] = "/tmp/wheel-hermes-desktop-validation"
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                docker,
                "compose",
                "-f",
                str(ROOT / "docker-compose.yml"),
                "-f",
                str(ROOT / "tests" / "app-proxy-validation.yml"),
                "--project-directory",
                tmp,
                "config",
                "--quiet",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if result.returncode != 0 and "Cannot connect to the Docker daemon" in (result.stderr or ""):
                self.skipTest("docker daemon not available")
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
