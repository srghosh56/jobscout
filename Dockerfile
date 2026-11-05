# syntax=docker/dockerfile:1.7
# Multi-stage build: (1) front end with Node/pnpm, (2) Python deps with uv,
# (3) slim non-root runtime serving API + static files.

# ---------- 1. front end ----------
FROM node:22.21.1-bookworm-slim AS web
WORKDIR /web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---------- 2. python deps ----------
FROM python:3.12.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
# Bake the CPU cross-encoder into the image so the container works offline.
ENV HF_HOME=/app/models
RUN /app/backend/.venv/bin/python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', device='cpu')"

# ---------- 3. runtime ----------
FROM python:3.12.13-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
WORKDIR /app
COPY --from=build --chown=app:app /app/backend /app/backend
COPY --from=build --chown=app:app /app/models /app/models
COPY --from=web --chown=app:app /web/dist /app/frontend/dist
COPY --chown=app:app config /app/config
COPY --chown=app:app deploy/entrypoint.sh /app/entrypoint.sh
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/models \
    HF_HUB_OFFLINE=1 \
    STATIC_DIR=/app/frontend/dist \
    PORT=8088
USER app
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8088\")}/healthz', timeout=4)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
