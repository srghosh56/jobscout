# Architecture

```mermaid
flowchart LR
  subgraph Sources
    BA[Bundesagentur für Arbeit<br/>Jobsuche API]
    AN[Arbeitnow API]
  end
  subgraph Pipeline["Data pipeline (make pipeline, or Dagster assets on a schedule)"]
    ING[ingest<br/>polite clients, cache,<br/>clean, upsert, MinHash dedup]
    EXT[extract<br/>LLM JSON-schema mode<br/>+ Pydantic validation]
    BC[bluecard + english_sufficient<br/>deterministic rules]
    EMB[embed<br/>1024-d vectors]
    INS[insights<br/>materialised views]
    EXP[expiry<br/>HEAD/GET, per-host limit]
  end
  subgraph DB["PostgreSQL 17 + pgvector"]
    P[(postings<br/>tsvector de/en, GIN<br/>vector HNSW cosine)]
    V[(allow-listed views<br/>read-only role)]
    O[(ingest_runs, feedback,<br/>saved_searches, api_keys)]
  end
  subgraph API["FastAPI (async)"]
    AUTH[API key + token bucket<br/>request IDs]
    SR[/search/]
    CH[/chat SSE/]
    CV[/cv/match/]
    IN[/insights, /feeds/]
    AG[LangGraph agent<br/>scope check → router → tools<br/>→ composer → citation check]
    TOOLS[8 tools: sql_query, search_postings,<br/>get_posting, compare_postings, match_cv,<br/>gap_analysis, bluecard_check, ask_clarification]
    HY[hybrid retrieval<br/>FTS + vector → RRF k=60<br/>→ cross-encoder top-8]
    G[SQL guard<br/>sqlglot AST allow-list]
  end
  DAG[Dagster<br/>weekly + daily schedules,<br/>retries, asset checks]
  SNAP[(Parquet snapshots<br/>DuckDB · Iceberg)]
  WEB[React + TS SPA<br/>served by FastAPI]
  MCP[MCP server<br/>stdio / streamable HTTP]
  LLM[(Chat / embedding model<br/>Ollama local · Mistral · Gemini)]
  OBS[OTel → Jaeger<br/>Prometheus → Grafana]

  BA --> ING
  AN --> ING
  ING --> P
  P --> EXT --> BC --> P
  P --> EMB --> P
  P --> INS --> V
  EXP --> P
  DAG -.runs.-> Pipeline
  P --> SNAP
  WEB --> AUTH --> SR & CH & CV & IN
  CH --> AG --> TOOLS
  TOOLS --> HY --> P
  TOOLS --> G --> V
  SR --> HY
  CV --> HY
  AG <--> LLM
  EXT <--> LLM
  EMB <--> LLM
  MCP --> TOOLS
  API -.-> OBS
```

## Orchestration
The pipeline functions run either by hand (`make pipeline`) or as Dagster assets (`jobscout/orchestration/`). Dagster adds per-source partitions, retries for network steps, blocking contract and privacy checks, a run-failure sensor with a Prometheus counter, and a columnar snapshot (Parquet → DuckDB, plus an Iceberg table) next to Postgres. The snapshot is for analytics across weeks, and Postgres stays the store that serves the app. It runs in Compose (code server, webserver and daemon; storage in a separate `dagster` database) and on kind with the official Helm chart (K8sRunLauncher, one Kubernetes Job per run). Details: [ORCHESTRATION.md](ORCHESTRATION.md).

## Request path of a chat question
1. Browser `POST /api/chat` with an API key → auth (SHA-256 hash lookup, constant-time compare) → token bucket → request ID.
2. The LangGraph `StateGraph` runs: **scope check** (bge-m3 compares the question with on-topic and off-topic examples; an off-topic question is refused before the LLM runs) → **route check** (a first question that only needs a role or skill search runs one search and goes straight to the composer) → **router** (LLM with function calling decides tools) → **tools** (independent calls run concurrently, then calls that need their results; invalid posting IDs and CV profiles are repaired) → back to router until it stops calling tools or hits the **6-step cap** → **composer** writes the answer from tool outputs (wrapped as untrusted data) → **citation check** (every `[posting_id]` must have been retrieved; one regeneration, then a warning).
3. Events stream back as SSE: `token`, `tool_call`, `tool_result`, `citation`, `done`.
4. Every step is an OpenTelemetry span; latency, tokens, tool errors and guard rejections are Prometheus metrics.

## Data model (main table `postings`)
Identity (`id`, `source`, `source_id`, `url`), cleaned text (`raw_text`, DB only, never committed), all extracted fields as typed columns, derived fields (`english_sufficient`, `bluecard_status`, `annual_salary_eur`), freshness (`is_active`, `last_checked_at`, `consecutive_failures`), provenance (`model_version`, `schema_version`, `content_hash`), search (`tsv_de`, `tsv_en`, `embedding vector(1024)`).

