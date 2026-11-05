#!/usr/bin/env bash
# Runs gitleaks from the project-local .tools/bin (downloaded by `make setup`),
# falling back to a gitleaks on PATH (CI uses the official action).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "$ROOT/.tools/bin/gitleaks" ]]; then
  exec "$ROOT/.tools/bin/gitleaks" "$@" --config "$ROOT/.gitleaks.toml"
elif command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks "$@" --config "$ROOT/.gitleaks.toml"
else
  echo "gitleaks not found: run 'make setup'" >&2; exit 1
fi
