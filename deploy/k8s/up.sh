#!/usr/bin/env bash
# Local Kubernetes edition: kind cluster "jobscout" + image load + Helm install.
# Leaves other kind clusters (e.g. gitops-demo) untouched.
set -euo pipefail
cd "$(dirname "$0")"
CLUSTER=jobscout NS=jobscout IMAGE=jobscout:local
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s
kubectl config use-context "kind-$CLUSTER" >/dev/null
docker image inspect "$IMAGE" >/dev/null 2>&1 || (cd ../.. && docker build -t "$IMAGE" .)
kind load docker-image "$IMAGE" --name "$CLUSTER"
helm lint charts/jobscout
helm upgrade --install jobscout charts/jobscout --namespace "$NS" --create-namespace --wait --timeout 15m
kubectl -n "$NS" rollout status deploy/jobscout --timeout=10m
echo "Ready. Port-forward with: kubectl -n $NS port-forward svc/jobscout 8089:80  → http://localhost:8089"
echo "Seed demo data: kubectl -n $NS exec deploy/jobscout -- jobscout seed"
