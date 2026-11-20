#!/usr/bin/env bash
# Dagster on the local kind cluster: official Helm chart + the JobScout user-code image.
# Needs the JobScout release first (make k8s-up): it provides Postgres and the app Secret.
# DAGSTER_PIPELINE_DB=<name> points the pipeline at another database in that Postgres
# (created and migrated here), e.g. to try it without touching the app database.
set -euo pipefail
cd "$(dirname "$0")"
CLUSTER=jobscout NS=jobscout IMAGE=jobscout-dagster:local
CHART_VERSION=1.13.31
PIPELINE_DB="${DAGSTER_PIPELINE_DB:-}"

kubectl config use-context "kind-$CLUSTER" >/dev/null
kubectl -n "$NS" get secret jobscout-secrets >/dev/null || { echo "run make k8s-up first"; exit 1; }

docker image inspect "$IMAGE" >/dev/null 2>&1 || (cd ../../.. && docker build -f deploy/dagster/Dockerfile -t "$IMAGE" .)
kind load docker-image "$IMAGE" --name "$CLUSTER"

# Databases (idempotent): Dagster storage, and the optional separate pipeline database.
psql_pg() { kubectl -n "$NS" exec jobscout-postgres-0 -- psql -U jobscout -d postgres -tAc "$1"; }
for db in dagster ${PIPELINE_DB}; do
  [ "$(psql_pg "SELECT 1 FROM pg_database WHERE datname = '$db'")" = "1" ] || psql_pg "CREATE DATABASE $db"
done

# Secrets, derived from the JobScout release's Secret (nothing is written to disk).
get() { kubectl -n "$NS" get secret jobscout-secrets -o "jsonpath={.data.$1}" | base64 -d; }
DB_URL=$(get DATABASE_URL) RO_URL=$(get READONLY_DATABASE_URL)
PG_PASSWORD=$(python3 -c 'import sys,urllib.parse as u;print(u.unquote(u.urlsplit(sys.argv[1]).password or ""))' "$DB_URL")
if [ -n "$PIPELINE_DB" ]; then
  DB_URL="${DB_URL%/*}/$PIPELINE_DB" RO_URL="${RO_URL%/*}/$PIPELINE_DB"
fi
kubectl -n "$NS" create secret generic dagster-postgresql-secret \
  --from-literal=postgresql-password="$PG_PASSWORD" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic jobscout-dagster-env \
  --from-literal=DATABASE_URL="$DB_URL" --from-literal=READONLY_DATABASE_URL="$RO_URL" \
  --from-literal=MISTRAL_API_KEY="$(get MISTRAL_API_KEY)" --from-literal=GEMINI_API_KEY="$(get GEMINI_API_KEY)" \
  --dry-run=client -o yaml | kubectl apply -f -

helm repo add dagster https://dagster-io.github.io/helm >/dev/null 2>&1 || true
helm repo update dagster >/dev/null
TMP=$(mktemp -d) && trap 'rm -rf "$TMP"' EXIT
helm pull dagster/dagster --version "$CHART_VERSION" --untar --untardir "$TMP"
helm lint "$TMP/dagster" -f values.yaml
helm upgrade --install dagster dagster/dagster --version "$CHART_VERSION" \
  --namespace "$NS" -f values.yaml --wait --timeout 15m

# Migrations are idempotent; run them from the user-code pod (it has the pipeline DB URL).
kubectl -n "$NS" rollout status deploy/dagster-dagster-user-deployments-jobscout-pipeline --timeout=5m
kubectl -n "$NS" exec deploy/dagster-dagster-user-deployments-jobscout-pipeline -- jobscout migrate
echo "Ready. kubectl -n $NS port-forward svc/dagster-dagster-webserver 3071:80  → http://localhost:3071"
