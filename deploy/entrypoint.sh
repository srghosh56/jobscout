#!/bin/sh
# Container entrypoint: apply migrations (idempotent), optionally seed, then serve.
set -e
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  jobscout migrate
fi
if [ "${SEED_ON_START:-false}" = "true" ]; then
  jobscout seed || echo "seed failed (continuing)"
fi
exec uvicorn jobscout.api.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8088}" --proxy-headers --forwarded-allow-ips="*"
