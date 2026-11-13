from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "app" / "serve_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("serve_bootstrap", MODULE_PATH)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class ConfigTests(unittest.TestCase):
    def test_private_ipv4_is_accepted(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "10.21.0.1",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            argv = bootstrap.serve_argv()
        self.assertEqual(argv[1], "serve")
        self.assertIn("--skip-build", argv)
        self.assertNotIn("gateway", argv)

    def test_loopback_ipv6_is_accepted(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "::1",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            argv = bootstrap.serve_argv()
        self.assertEqual(argv[3], "::1")

    def test_wildcard_listener_is_rejected(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "0.0.0.0",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "wildcard"):
                bootstrap.serve_argv()

    def test_ipv6_wildcard_is_rejected(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "::",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "wildcard"):
                bootstrap.serve_argv()

    def test_public_endpoint_is_rejected(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "8.8.8.8",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "private"):
                bootstrap.serve_argv()

    def test_missing_settings_are_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "HERMES_DESKTOP_LISTEN_HOST"):
                bootstrap.serve_argv()


class ExecTests(unittest.TestCase):
    def test_main_execs_hermes_serve_only(self) -> None:
        values = {
            "HERMES_DESKTOP_LISTEN_HOST": "127.0.0.1",
            "HERMES_DESKTOP_LISTEN_PORT": "9121",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch.object(bootstrap.os.path, "isfile", return_value=True):
                with patch.object(bootstrap.os, "execv") as execv:
                    bootstrap.main()
        execv.assert_called_once()
        argv = execv.call_args.args[1]
        self.assertEqual(argv[1], "serve")
        self.assertEqual(argv[-1], "--skip-build")
        self.assertNotIn("gateway", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
