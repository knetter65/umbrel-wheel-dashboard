# Hermes Desktop companion for umbrelOS

Private `hermes serve` backend, lifecycle-independent from the vendor-managed Hermes compose.

## Why this exists

Hermes app updates replace the official compose. A Desktop backend injected into that file disappears after updates. This companion keeps `hermes serve` in its own Umbrel app.

This is not a TCP relay. It must use the current official Hermes image and share the Hermes home.

## Security properties

- Binds only an explicitly configured private, loopback, or link-local IP.
- Rejects wildcard, multicast, and public listeners.
- Starts `hermes serve --skip-build` only; it does not start `hermes gateway run`.
- Runs as UID/GID 1000 with all Linux capabilities dropped.
- Mounts only the Hermes home and the isolated Hermes Vault; the parent Obsidian vault stays unmounted.
- Listen host and port are injected through umbrelOS app settings after install. They are not committed to Git.

## Required service environment

Set these on the `serve` service through umbrelOS app settings:

- `HERMES_DESKTOP_LISTEN_HOST`
- `HERMES_DESKTOP_LISTEN_PORT`

## Staging

Keep the existing Hermes-compose `desktop_bridge` as production until:

1. this app is installed and configured on a non-production listen port;
2. container health is healthy;
3. local `/api/status` succeeds on the staging port;
4. a managed restart preserves settings;
5. Hermes Desktop completes login plus WebSocket from the Mac;
6. only then cut over the production listen port and remove `desktop_bridge` from the Hermes compose.

## Image tracking

This package pins the official Hermes Umbrel image digest. After an official Hermes app update, bump this companion to the new digest and re-verify serve-only startup before trusting Desktop.
