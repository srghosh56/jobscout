# Kubernetes edition (local, kind + Helm + Argo CD)

Same patterns as `gitops-microservice-demo`: a Helm chart with non-root pods, read-only root filesystem, dropped capabilities, readiness `/readyz` and liveness `/healthz` probes, rolling updates with `maxUnavailable: 0`, an optional ServiceMonitor/PrometheusRule/Grafana-dashboard ConfigMap for kube-prometheus-stack, and an Argo CD `Application` with automated prune + self-heal.

```bash
make k8s-up      # kind cluster "jobscout" + kind load + helm upgrade --install (Postgres in-cluster)
kubectl -n jobscout port-forward svc/jobscout 8089:80   # http://localhost:8089
kubectl -n jobscout exec deploy/jobscout -- jobscout seed   # synthetic demo data (no API calls)
make k8s-down    # delete the cluster (other kind clusters are untouched)
```

| Piece | File |
|---|---|
| API Deployment (uid 10001, `RUN_MIGRATIONS=false`) | `charts/jobscout/templates/deployment.yaml` |
| Migration Job (plain Job per release revision; Argo CD Sync hook) | `templates/migrate-job.yaml` |
| Postgres 17 + pgvector StatefulSet + PVC (dev only) | `templates/postgres.yaml` (`postgres.enabled`) |
| Secret (local values) or `secrets.existingSecret` | `templates/secret.yaml` |
| ServiceMonitor / PrometheusRule / Grafana dashboard | disabled by default (need kube-prometheus-stack) |
| Argo CD Application | `argocd/application.yaml` |

**LLM in the cluster:** pods use `http://host.docker.internal:11434` (Ollama on the Mac host via Docker Desktop). Set `app.llmProvider=mistral` and `secrets.mistralApiKey` (or an `existingSecret`) to use a hosted model instead.

**Argo CD:** install Argo CD in the cluster, then `kubectl apply -f deploy/k8s/argocd/application.yaml`. This manifest has not been applied yet.

**Scaling beyond 1 replica:** needs a shared LangGraph checkpointer and a shared rate-limit store.

## Dagster (pipeline orchestration)

`make k8s-dagster-up` (after `make k8s-up`) installs the official Dagster chart (`dagster/dagster` 1.13.31 from https://dagster-io.github.io/helm) into the `jobscout` namespace with `dagster/values.yaml`:
- the webserver and daemon;
- a `jobscout-pipeline` user-code deployment (`jobscout-dagster:local`, loaded into kind);
- `K8sRunLauncher`, one Kubernetes Job per run.

Dagster's storage is a `dagster` database in the JobScout Postgres. `dagster/up.sh` creates the two Secrets with `kubectl create secret` from the JobScout release Secret, so no secret is in a values file. It also runs `helm lint` on the pulled chart, installs it, and applies migrations from the user-code pod. `DAGSTER_PIPELINE_DB=<name>` points the pipeline at a separate database. `make k8s-dagster-down` removes the release and the Secrets. Details and recorded runs: [docs/ORCHESTRATION.md](../../docs/ORCHESTRATION.md).
