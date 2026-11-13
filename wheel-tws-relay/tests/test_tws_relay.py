from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

MODULE_PATH = Path(__file__).parents[1] / "app" / "tws_relay_v012.py"
SPEC = importlib.util.spec_from_file_location("tws_relay", MODULE_PATH)
assert SPEC and SPEC.loader
relay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay_module)


class ConfigTests(unittest.TestCase):
    def test_private_endpoints_are_accepted(self) -> None:
        values = {
            "TWS_RELAY_LISTEN_HOST": "127.0.0.1",
            "TWS_RELAY_LISTEN_PORT": "7496",
            "TWS_RELAY_REMOTE_HOST": "127.0.0.1",
            "TWS_RELAY_REMOTE_PORT": "7498",
        }
        with patch.dict(os.environ, values, clear=True):
            config = relay_module.load_config()
        self.assertEqual(config["max_connections"], 8)
        self.assertEqual(config["listen_port"], 7496)

    def test_wildcard_listener_is_rejected(self) -> None:
        values = {
            "TWS_RELAY_LISTEN_HOST": "0.0.0.0",
            "TWS_RELAY_LISTEN_PORT": "7496",
            "TWS_RELAY_REMOTE_HOST": "127.0.0.1",
            "TWS_RELAY_REMOTE_PORT": "7498",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "wildcard"):
                relay_module.load_config()

    def test_public_endpoint_is_rejected(self) -> None:
        values = {
            "TWS_RELAY_LISTEN_HOST": "127.0.0.1",
            "TWS_RELAY_LISTEN_PORT": "7496",
            "TWS_RELAY_REMOTE_HOST": "8.8.8.8",
            "TWS_RELAY_REMOTE_PORT": "7498",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError, "private"):
                relay_module.load_config()


class RelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_is_forwarded_bidirectionally(self) -> None:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            payload = await reader.read(1024)
            writer.write(payload[::-1])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]
        config = {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "remote_host": "127.0.0.1",
            "remote_port": upstream_port,
            "connect_timeout": 2.0,
            "max_connections": 2,
        }
        relay = relay_module.Relay(config)
        relay.publish = AsyncMock()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        try:
            relay_port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
            writer.write(b"abc123")
            await writer.drain()
            self.assertEqual(await reader.read(1024), b"321cba")
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.05)
            self.assertEqual(relay.accepted_total, 1)
            self.assertEqual(relay.upstream_failures, 0)
            self.assertGreaterEqual(relay.publish.await_count, 2)
        finally:
            server.close()
            await server.wait_closed()
            upstream.close()
            await upstream.wait_closed()


if __name__ == "__main__":
    unittest.main()
