# JobScout - one entry point for every workflow. Run `make help`.
SHELL := /bin/bash
ROOT := $(shell pwd)
# Prefer the project-local arm64 Python 3.12; fall back to uv's resolution.
LOCAL_PY := $(firstword $(wildcard $(ROOT)/.tools/python/cpython-3.12*-aarch64-none/bin/python3.12))
export UV_PYTHON ?= $(if $(LOCAL_PY),$(LOCAL_PY),3.12)
export UV_PYTHON_INSTALL_DIR := $(ROOT)/.tools/python
export COREPACK_HOME := $(ROOT)/.tools/corepack
export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
UV := cd backend && uv run --frozen
JS := uv run --frozen --project backend jobscout
PNPM := cd frontend && corepack pnpm
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env
GITLEAKS_VERSION := 8.30.1

PHONY_EXTRA := golden dataset-stats eval-report db-up salary-refresh scope-refresh judge-export judge-agreement
.PHONY: help setup gitleaks-install up down logs migrate seed ingest extract embed expiry insights \
	serve web build-web openapi test test-backend test-frontend coverage lint lint-backend lint-frontend \
	gitleaks eval-smoke eval-full eval-record ablations retrieval-bench scope-bench route-bench \
	feedback-review mcp mcp-http k8s-up k8s-down gcp-plan pipeline

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: gitleaks-install ## install backend (uv) + frontend (pnpm) deps, pre-commit hook
	cd backend && uv sync --frozen
	$(PNPM) install --frozen-lockfile --store-dir $(ROOT)/.tools/pnpm-store
	cd backend && uv run pre-commit install -c ../.pre-commit-config.yaml || true

gitleaks-install: ## download gitleaks into .tools/bin (project-local, checksum-verified)
	@scripts/install_gitleaks.sh $(GITLEAKS_VERSION)

# ---------- local stack ----------
up: ## docker compose: postgres+pgvector, api (+web), jaeger, prometheus, grafana; seeds from fixtures
	$(COMPOSE) up -d --build
	@echo "API+web: http://localhost:8088  Jaeger: http://localhost:16686  Prometheus: http://localhost:9091  Grafana: http://localhost:3001"

down: ## stop the local stack
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

db-up: ## start only Postgres (for development and tests)
	$(COMPOSE) up -d postgres

migrate: ## alembic upgrade head
	$(JS) migrate

seed: ## load recorded fixtures (no network) and run extraction/embedding from cache
	$(JS) seed

# ---------- data pipeline ----------
ingest: ## fetch postings from both APIs (polite, cached) and upsert
	$(JS) ingest

extract: ## LLM extraction (JSON-schema mode) for postings without extraction
	$(JS) extract

salary-refresh: ## recompute salary and Blue Card columns from posting text, then insights
	$(JS) extract --salaries-only
	$(JS) insights

embed: ## embeddings for postings without vectors
	$(JS) embed

expiry: ## check posting URLs, mark inactive after 2 consecutive failures
	$(JS) expiry

scope-refresh: ## retire postings from Arbeitnow's foreign sites, then refresh insights
	$(JS) scope-refresh
	$(JS) insights

insights: ## refresh insights materialised views
	$(JS) insights

pipeline: ingest extract embed insights ## full weekly pipeline

# ---------- app ----------
serve: ## run the API with reload on :8088
	cd backend && uv run --frozen uvicorn jobscout.api.app:create_app --factory --reload --port 8088

web: ## run the Vite dev server on :5173
	$(PNPM) dev

build-web:
	$(PNPM) build

openapi: ## export OpenAPI JSON and regenerate the typed TS client
	$(JS) openapi > docs/openapi.json
	$(PNPM) gen:api

# ---------- quality ----------
test: test-backend test-frontend ## all tests

test-backend:
	$(UV) pytest --cov=jobscout --cov-report=term-missing --cov-fail-under=80

test-frontend:
	$(PNPM) test

lint: lint-backend lint-frontend gitleaks ## all linters + type checks + secret scan

lint-backend:
	$(UV) ruff check src tests ../eval
	$(UV) ruff format --check src tests ../eval
	$(UV) mypy

lint-frontend:
	$(PNPM) lint
	$(PNPM) typecheck

gitleaks: ## scan git history + working tree for secrets
	scripts/gitleaks.sh git --redact --no-banner .
	scripts/gitleaks.sh dir --redact --no-banner . || true

# ---------- evaluation ----------
golden: ## (re)build eval/golden.yaml from definitions + current DB
	$(UV) python ../eval/build_golden.py

dataset-stats: ## coverage, sources, rules-vs-LLM agreement → eval/results/dataset_stats.json
	$(UV) python ../eval/dataset_stats.py

eval-report: ## regenerate the metric tables in docs/EVALUATION.md from eval/results
	$(UV) python ../eval/report.py

retrieval-bench: ## recall@5 / MRR / nDCG@10 on golden retrieval questions
	$(UV) python ../eval/retrieval_bench.py

eval-smoke: ## 20-question smoke eval replayed from cassettes (no API calls)
	$(UV) python ../eval/run_eval.py --smoke --replay

eval-record: ## re-record the smoke cassettes against the real model
	$(UV) python ../eval/run_eval.py --smoke --record

eval-full: ## full 80-question eval against the real model
	$(UV) python ../eval/run_eval.py --full

judge-export: ## write 20 answers from the latest full run to eval/labels/judge_check.csv
	$(UV) python ../eval/judge_agreement.py export

judge-agreement: ## agreement and Cohen's kappa between the judge and hand labels
	$(UV) python ../eval/judge_agreement.py score

scope-bench: ## scope check accuracy on unseen questions (needs the embedding model)
	$(UV) python ../eval/scope_bench.py

route-bench: ## single-shot routing accuracy on golden questions (needs the embedding model)
	$(UV) python ../eval/route_bench.py

ablations: ## retrieval + agent ablations (+ Gemini/supervisor when GEMINI_API_KEY is set)
	$(UV) python ../eval/ablations.py

feedback-review: ## list negative feedback with traces; promote approved items to golden.yaml
	$(JS) feedback-review

# ---------- integrations ----------
mcp: ## MCP server over stdio
	$(JS) mcp --transport stdio

mcp-http: ## MCP server over streamable HTTP on :8765
	$(JS) mcp --transport streamable-http

# ---------- infrastructure ----------
k8s-up: ## kind cluster + Helm install (local only)
	deploy/k8s/up.sh

k8s-down:
	kind delete cluster --name jobscout

gcp-plan: ## terraform plan only
	cd deploy/gcp && terraform init -input=false && terraform plan -input=false -var-file=terraform.tfvars
