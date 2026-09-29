# Wheel Dashboard

Wheel Dashboard is a read-only dashboard for inspecting sanitized Wheel-strategy snapshots. The repository contains only synthetic fixtures and has no broker connectivity, credentials, account identifiers, order endpoint, or write route.

## Safety model

- Read-only HTTP application behind Umbrel's authenticated `app_proxy`.
- No published host port.
- Runtime user `1000:1000`, read-only root filesystem, all Linux capabilities dropped, and `no-new-privileges`.
- External data is mounted read-only at `/data`.
- The public contract uses namespace `wheel-dashboard`; deployments must not mix data roots across strategies.
- The bundled `ALFA`, `BETA`, and `GAMMA` records are deterministic synthetic fixtures, not market or account data.

## Local verification

    uv sync --frozen --dev
    uv run python -m unittest discover -s tests -v
    uv run python -m compileall -q src tests scripts
    uv run python scripts/scan_publication.py .

## Multi-architecture image

The Dockerfile uses base images that publish `linux/arm64` and `linux/amd64` manifests. Build the release image for both platforms with Buildx. Do not publish until the repository owner, exact image contents, scan results, and publish approval are recorded. After GHCR publication, resolve the registry distribution digest and replace the temporary tag in `umbrel/wheel-dashboard/docker-compose.yml` with `ghcr.io/<owner>/wheel-dashboard@sha256:<distribution-digest>`.

## Data contract

`schema/dashboard_snapshot.schema.json` is the strict public contract. Unknown fields are rejected. Account identifiers and secret fields are not part of the schema.
