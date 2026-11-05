#!/usr/bin/env bash
# Post-deploy smoke test. Usage: deploy/smoke_test.sh https://<user>-jobscout.hf.space [API_KEY]
# Without API_KEY a demo key is requested (requires DEMO_KEY_ENABLED=true).
set -euo pipefail
BASE="${1:?base URL}"; KEY="${2:-}"
pass() { echo "✔ $1"; }; fail() { echo "✘ $1"; exit 1; }
curl -fsS "$BASE/healthz" | grep -q '"ok"' && pass healthz || fail healthz
curl -fsS "$BASE/readyz" | grep -q '"ready"' && pass readyz || fail readyz
curl -fsS "$BASE/" | grep -q "<title>JobScout" && pass "web UI" || fail "web UI"
if [[ -z "$KEY" ]]; then
  KEY=$(curl -fsS -X POST "$BASE/api/demo-key" | python3 -c 'import sys,json;print(json.load(sys.stdin)["api_key"])') && pass "demo key"
fi
curl -fsS -X POST "$BASE/api/search" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"data engineer","filters":{"english_sufficient":true},"top_k":3}' | grep -q '"results"' && pass search || fail search
curl -fsS "$BASE/api/insights" | grep -q '"methodology"' && pass insights || fail insights
curl -fsS -N -X POST "$BASE/api/chat" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"message":"How many active postings are in Berlin?"}' --max-time 120 | grep -q "event: done" && pass "chat stream" || fail "chat stream"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/search" -H 'Content-Type: application/json' -d '{"query":"x"}')
[[ "$code" == "401" ]] && pass "auth enforced" || fail "auth not enforced ($code)"
echo "all smoke checks passed"
