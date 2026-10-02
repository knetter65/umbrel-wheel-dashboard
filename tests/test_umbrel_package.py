from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "umbrel" / "wheel-dashboard"
STORE_ROOT_PACKAGE = ROOT / "wheel-dashboard"
STORE_DESCRIPTOR = ROOT / "umbrel-app-store.yml"
NESTED_STORE_DESCRIPTOR = ROOT / "umbrel" / "umbrel-app-store.yml"
STORE_PACKAGE_FILES = (
    "docker-compose.yml",
    "umbrel-app.yml",
    "icon.png",
    "gallery/1.png",
)

class UmbrelPackageTests(unittest.TestCase):
    def test_identity_and_assets(self):
        manifest = (PACKAGE / "umbrel-app.yml").read_text()
        for value in ("id: wheel-dashboard", "name: Wheel Dashboard", 'version: "0.3.1"', "port: 8050"):
            self.assertIn(value, manifest)
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn('org.opencontainers.image.version="0.3.1"', dockerfile)
        for path in (PACKAGE / "icon.png", PACKAGE / "gallery" / "1.png"):
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 1000)

    def test_community_store_root_layout_matches_nested_package(self):
        self.assertTrue(STORE_DESCRIPTOR.is_file())
        self.assertEqual(STORE_DESCRIPTOR.read_bytes(), NESTED_STORE_DESCRIPTOR.read_bytes())
        store = STORE_DESCRIPTOR.read_text()
        self.assertIn("id: wheel", store)
        self.assertIn("name: Wheel Dashboard Community App Store", store)
        self.assertTrue((STORE_ROOT_PACKAGE / "umbrel-app.yml").is_file())
        for relative in STORE_PACKAGE_FILES:
            nested = PACKAGE / relative
            root_copy = STORE_ROOT_PACKAGE / relative
            self.assertTrue(root_copy.is_file(), relative)
            self.assertEqual(nested.read_bytes(), root_copy.read_bytes(), relative)

    def test_hardened_authenticated_proxy(self):
        compose = (PACKAGE / "docker-compose.yml").read_text()
        self.assertIn("app_proxy:", compose)
        self.assertIn("APP_HOST: wheel-dashboard_server_1", compose)
        self.assertNotIn("PROXY_AUTH_ADD", compose)
        self.assertNotIn("PROXY_AUTH_WHITELIST", compose)
        self.assertNotIn("\n    ports:", compose)
        self.assertIn("${APP_DATA_DIR}/data:/data:ro", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn('WHEEL_DASHBOARD_READ_ONLY: "1"', compose)
        self.assertIn("cap_drop:\n      - ALL", compose)
        self.assertIn("no-new-privileges:true", compose)

    def test_image_is_pinned_to_verified_distribution_digest(self):
        compose = (PACKAGE / "docker-compose.yml").read_text()
        self.assertIn(
            "ghcr.io/knetter65/wheel-dashboard@sha256:89b9ee1762d1498b42ec8b0afd680e89c016a4d80eeb2cdbf2a9ad627e0e016d",
            compose,
        )

    def test_context_excludes_private_inputs(self):
        ignored = set((ROOT / ".dockerignore").read_text().splitlines())
        self.assertTrue({"data", "docs", "tests", ".env"}.issubset(ignored))
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertNotIn("COPY data", dockerfile)
        self.assertIn("USER 1000:1000", dockerfile)
        self.assertIn("WHEEL_DASHBOARD_READ_ONLY=1", dockerfile)
        self.assertGreaterEqual(dockerfile.count("@sha256:"), 2)

if __name__ == "__main__":
    unittest.main()
