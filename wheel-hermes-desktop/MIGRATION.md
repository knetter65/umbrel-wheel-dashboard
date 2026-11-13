# Hermes Desktop companion migration

This record omits private network addresses, device identifiers, credentials, and account data.

## Current production contract

- Owner: vendor-managed Hermes Agent compose (temporary).
- Service name: `desktop_bridge`.
- Runtime image: the official Hermes Umbrel image then in use.
- Process: `hermes serve --skip-build` only; no messaging gateway.
- Network mode: host, private listen address injected at runtime.
- Data: shared Hermes home plus isolated Hermes Vault. Parent Obsidian vault stays unmounted.

## Why a companion app

Hermes app updates replace the vendor compose. A Desktop backend injected there disappears after updates. `hermes serve` cannot move to a generic Python image because it is the same agent runtime.

## Target contract

- App ID: `wheel-hermes-desktop`.
- Pinned official Hermes image digest, bumped after each official Hermes update.
- Entrypoint bypasses s6 and execs `hermes serve --skip-build`.
- Listen host and port supplied only as local Umbrel app settings.
- Status page on the default Docker network for `app_proxy`.
- Existing `hermes-desktop-bridge` remains production until Desktop login plus WebSocket succeed on a staging port.

## Cutover gates

- [ ] Package tests and publication scan pass.
- [ ] App is installed on a non-production listen port.
- [ ] Container health is healthy and serve-only.
- [ ] Local `/api/status` succeeds on the staging port.
- [ ] Managed restart preserves settings.
- [ ] Hermes Desktop completes login plus WebSocket from the Mac.
- [ ] Production listen port is moved only after those gates.
- [ ] Hermes compose no longer contains `desktop_bridge`.
- [ ] Post-update guard does not re-inject `desktop_bridge`.
