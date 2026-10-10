ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754
ARG PYTHON_IMAGE=python:3.13.5-slim@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}

ARG SOURCE_COMMIT
LABEL org.opencontainers.image.source="https://github.com/knetter65/umbrel-wheel-dashboard" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="0.3.4"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    WHEEL_DASHBOARD_DATA_DIR=/data \
    WHEEL_DASHBOARD_READ_ONLY=1 \
    WHEEL_DASHBOARD_HOST=0.0.0.0 \
    WHEEL_DASHBOARD_PORT=8050

WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY --chown=1000:1000 src ./src
COPY --chown=1000:1000 schema ./schema
COPY --chown=1000:1000 assets ./assets
COPY --chown=1000:1000 fixtures ./fixtures
RUN WHEEL_DASHBOARD_DATA_DIR=/app/fixtures WHEEL_DASHBOARD_READ_ONLY=0 /app/.venv/bin/python -c "from src.database import ensure_fixture_database; ensure_fixture_database()" \
    && chmod 0444 /app/fixtures/phase1-fixture.db \
    && rm -f /app/fixtures/phase1-fixture.db-shm /app/fixtures/phase1-fixture.db-wal

USER 1000:1000
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=3).read()"]
CMD ["/app/.venv/bin/python", "-m", "src.app"]
