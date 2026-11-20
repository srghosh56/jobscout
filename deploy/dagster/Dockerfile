# syntax=docker/dockerfile:1.7
# Dagster image: the same backend as the API image plus the "orchestration" extra.
# One image runs the code server (JobScout definitions), the webserver and the daemon in
# Compose, and the user-code deployment on Kubernetes. The API image stays unchanged.

# ---------- 1. python deps ----------
FROM python:3.12.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra orchestration --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --extra orchestration

# ---------- 2. runtime ----------
FROM python:3.12.13-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
WORKDIR /app
COPY --from=build --chown=app:app /app/backend /app/backend
COPY --chown=app:app config /app/config
COPY --chown=app:app deploy/dagster/dagster.yaml deploy/dagster/workspace.yaml /opt/dagster/dagster_home/
# Writable folders: Parquet/DuckDB snapshots and the HTTP cache (mounted in Compose).
RUN mkdir -p /app/.cache/snapshots /app/.cache/http \
    && chown -R app:app /app/.cache /opt/dagster/dagster_home
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home
# Numeric user (the "app" user above), so Kubernetes runAsNonRoot can verify it.
USER 10001:10001
EXPOSE 4000 3070 9465
# Code server by default; Compose and Helm override the command for webserver/daemon.
CMD ["dagster", "api", "grpc", "-m", "jobscout.orchestration.definitions", "-h", "0.0.0.0", "-p", "4000"]
