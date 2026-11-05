# Hugging Face Space (Docker SDK). Copy this file to the Space repo root as `Dockerfile`
# (see docs/DEPLOYMENT_GUIDE.md). Differences from the main image: port 7860, uid 1000
# (Spaces run containers as user 1000), bge-m3 baked in for CPU query embeddings
# (EMBED_PROVIDER=st), no Ollama: the chat model comes from a hosted API key secret.
FROM node:22.21.1-bookworm-slim AS web
WORKDIR /web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never HF_HOME=/app/models
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
RUN .venv/bin/python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', device='cpu'); SentenceTransformer('BAAI/bge-m3', device='cpu')"

FROM python:3.12.13-slim-bookworm
RUN useradd --uid 1000 --create-home user
WORKDIR /app
COPY --from=build --chown=user /app/backend /app/backend
COPY --from=build --chown=user /app/models /app/models
COPY --from=web --chown=user /web/dist /app/frontend/dist
COPY --chown=user config /app/config
COPY --chown=user deploy/entrypoint.sh /app/entrypoint.sh
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HOME=/app/models HF_HUB_OFFLINE=1 \
    STATIC_DIR=/app/frontend/dist PORT=7860 EMBED_PROVIDER=st RUN_MIGRATIONS=false \
    LOG_JSON=true OTEL_ENABLED=false
USER user
EXPOSE 7860
ENTRYPOINT ["/app/entrypoint.sh"]
