#!/usr/bin/env python3
"""Validate private listen settings and exec hermes serve --skip-build."""
from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path

HERMES_BIN = "/opt/hermes/.venv/bin/hermes"
HOST_ENV = "HERMES_DESKTOP_LISTEN_HOST"
PORT_ENV = "HERMES_DESKTOP_LISTEN_PORT"
VAULT_TARGET = "/obsidian/Obsidian Vault/Hermes Vault"
MOUNTINFO = "/proc/self/mountinfo"


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


def parse_port(name: str) -> str:
    raw = required_env(name)
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer port") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{name} is out of range")
    return str(port)


def _unescape_mountinfo_path(value: str) -> str:
    for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, literal)
    return value


def exact_mount_targets(mountinfo: str = MOUNTINFO) -> set[str]:
    targets: set[str] = set()
    with open(mountinfo, encoding="utf-8") as handle:
        for line in handle:
            fields = line.split(" - ", 1)[0].split()
            if len(fields) >= 5:
                targets.add(_unescape_mountinfo_path(fields[4]))
    return targets


def validate_vault_mount(target: str = VAULT_TARGET) -> None:
    path = Path(target)
    if not path.is_dir():
        raise ValueError("managed Hermes Vault mount is missing")
    if target not in exact_mount_targets():
        raise ValueError("Hermes Vault path is not an exact managed mount")
    if not os.access(target, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("managed Hermes Vault mount is not read-write accessible")


def serve_argv() -> list[str]:
    host = parse_private_ip(HOST_ENV)
    port = parse_port(PORT_ENV)
    return [HERMES_BIN, "serve", "--host", host, "--port", port, "--skip-build"]


def main() -> None:
    argv = serve_argv()
    if not os.path.isfile(HERMES_BIN):
        raise SystemExit("hermes binary missing from image")
    validate_vault_mount()
    os.execv(HERMES_BIN, argv)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
