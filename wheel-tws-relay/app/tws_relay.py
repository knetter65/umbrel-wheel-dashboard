#!/usr/bin/env python3
"""Private, payload-blind TCP relay for a read-only TWS endpoint."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import signal
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(os.environ.get("TWS_RELAY_STATE_PATH", "/state/relay-status.json"))
HEALTH_SOCKET = Path(os.environ.get("TWS_RELAY_HEALTH_SOCKET", "/tmp/tws-relay-health.sock"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def parse_private_ip(name: str) -> str:
    value = required_env(name)
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an IP address") from exc
    if address.is_unspecified or address.is_multicast:
        raise ValueError(f"{name} may not be a wildcard or multicast address")
    if not (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError(f"{name} must be private, loopback, or link-local")
    return value


def parse_port(name: str) -> int:
    raw = required_env(name)
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def parse_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def load_config() -> dict[str, object]:
    return {
        "listen_host": parse_private_ip("TWS_RELAY_LISTEN_HOST"),
        "listen_port": parse_port("TWS_RELAY_LISTEN_PORT"),
        "remote_host": parse_private_ip("TWS_RELAY_REMOTE_HOST"),
        "remote_port": parse_port("TWS_RELAY_REMOTE_PORT"),
        "connect_timeout": float(os.environ.get("TWS_RELAY_CONNECT_TIMEOUT", "5")),
        "max_connections": parse_positive_int("TWS_RELAY_MAX_CONNECTIONS", 8),
    }


def atomic_write_state(payload: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE_PATH)
    os.chmod(STATE_PATH, 0o600)


class Relay:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.active_connections = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.upstream_failures = 0
        self._counter_lock = asyncio.Lock()

    def state(self, status: str = "ready") -> dict[str, object]:
        return {
            "status": status,
            "updated_at": utc_now(),
            "active_connections": self.active_connections,
            "accepted_total": self.accepted_total,
            "rejected_total": self.rejected_total,
            "upstream_failures": self.upstream_failures,
            "payload_logging": False,
        }

    def publish(self, status: str = "ready") -> None:
        atomic_write_state(self.state(status))

    async def pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        remote_writer: asyncio.StreamWriter | None = None
        admitted = False
        async with self._counter_lock:
            if self.active_connections >= int(self.config["max_connections"]):
                self.rejected_total += 1
            else:
                self.active_connections += 1
                self.accepted_total += 1
                admitted = True
            self.publish()
        if not admitted:
            client_writer.close()
            await client_writer.wait_closed()
            return
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    str(self.config["remote_host"]), int(self.config["remote_port"])
                ),
                timeout=float(self.config["connect_timeout"]),
            )
            forward = asyncio.create_task(self.pipe(client_reader, remote_writer))
            reverse = asyncio.create_task(self.pipe(remote_reader, client_writer))
            done, pending = await asyncio.wait(
                {forward, reverse}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except (OSError, asyncio.TimeoutError):
            self.upstream_failures += 1
            print(json.dumps({"event": "upstream_connect_failed"}), flush=True)
        finally:
            if remote_writer is not None:
                remote_writer.close()
                with suppress(ConnectionError):
                    await remote_writer.wait_closed()
            client_writer.close()
            with suppress(ConnectionError):
                await client_writer.wait_closed()
            async with self._counter_lock:
                self.active_connections -= 1
                self.publish()


async def health_handler(
    _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"ok\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def run() -> None:
    try:
        config = load_config()
    except (ValueError, OverflowError) as exc:
        atomic_write_state(
            {
                "status": "configuration_error",
                "updated_at": utc_now(),
                "error": str(exc),
                "payload_logging": False,
            }
        )
        raise

    relay = Relay(config)
    HEALTH_SOCKET.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_SOCKET.unlink(missing_ok=True)
    health_server = await asyncio.start_unix_server(health_handler, path=str(HEALTH_SOCKET))
    os.chmod(HEALTH_SOCKET, 0o600)
    relay_server = await asyncio.start_server(
        relay.handle,
        str(config["listen_host"]),
        int(config["listen_port"]),
        backlog=16,
    )
    relay.publish()
    print(json.dumps({"event": "relay_ready", "payload_logging": False}), flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with health_server, relay_server:
            await stop.wait()
    finally:
        relay.publish("stopping")
        HEALTH_SOCKET.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(run())
