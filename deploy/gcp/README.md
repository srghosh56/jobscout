# Google Cloud edition (Terraform, plan only)

`make gcp-plan` runs `terraform init` + `terraform plan`. **Never `apply` from this repo without a review.** The code was validated with `terraform validate` against `hashicorp/google` 8.9; `plan` hasn't been run because it needs your GCP credentials and project.

## What it creates
| File | Resources | Why |
|---|---|---|
| `budget.tf` | API enablement, e-mail notification channel, **billing budget** (50/90/100% actual + 100% forecast) | Cost guardrail first; Cloud Run services `depends_on` the budget |
| `kms.tf` | Key ring + 2 keys (90-day rotation, `prevent_destroy`), service-agent grants | CMEK for Artifact Registry and the eval-report bucket |
| `registry.tf` | Artifact Registry (Docker, CMEK, keep last 10), eval-report bucket (CMEK, versioned, public access prevented, 180-day lifecycle) | Images; CI evaluation reports |
| `secrets.tf` | Secret Manager containers `jobscout-database-url`, `-readonly-database-url`, `-gemini-api-key`, `-mistral-api-key` | Values are added by hand, never in Terraform state |
| `iam.tf` | SAs `jobscout-run`, `llm-gateway-run`, `github-deployer` with least privilege | Per-secret accessor bindings; only jobscout may invoke the gateway; the deployer can push to one repo and deploy two services |
| `network.tf` | VPC + /24 subnet with Private Google Access | **Direct VPC egress** for Cloud Run (no Serverless VPC connector) |
| `run.tf` | Cloud Run v2 `llm-gateway` (**internal ingress**) and `jobscout` (public, startup/liveness probes, secrets from Secret Manager) | |
| `monitoring.tf` | Uptime check on `/healthz`, alerts: uptime failing, 5xx rate, p95 latency | |
| `wif.tf` | Workload Identity pool + GitHub OIDC provider restricted to `repo` + `refs/heads/main`, impersonation binding | GitHub Actions without JSON keys (`.github/workflows/deploy-gcp.yml`) |

## Steps
1. Create a project, link billing, `gcloud auth application-default login`.
2. `cp terraform.tfvars.example terraform.tfvars` and fill in the values (git-ignored).
3. `make gcp-plan` and read the plan.
4. Only after review: `terraform apply` (by you), then add secret versions:
   `printf '%s' "$DATABASE_URL" | gcloud secrets versions add jobscout-database-url --data-file=-`.
5. Set the repository variables `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER`, `DEPLOYER_SA` from the outputs and run the `deploy-gcp` workflow.

## Notes
- `llm-gateway` is a separate project; JobScout gets its URL as `LLM_GATEWAY_URL` but **currently calls model providers directly**. Routing through the gateway is a future integration, not implemented here.
- The image for Cloud Run is the slim image in `deploy/spaces/Dockerfile` (`EMBED_PROVIDER=st`, bge-m3 baked in), so no Ollama is needed.
- With `min_instance_count = 0`, cold starts load two models (~20–60 s, not measured on Cloud Run).
