# Orchestration with Dagster

JobScout has two ways to run the data pipeline:

- **Simple path:** `make pipeline` runs ingest → extract → embed → insights from the command line. Nothing is scheduled or recorded beyond the `ingest_runs` table.
- **Scheduled, observable path:** Dagster runs the same functions as software-defined assets. It schedules them weekly, retries network steps, records per-run metadata, and stops on failed data-quality and privacy checks. It also writes a columnar snapshot for analytics.

Both paths call the same code (`jobscout.ingest.run`, `jobscout.extract.runner`, `jobscout.embed`, `jobscout.expiry`, `jobscout.insights`), so they can't drift apart. Dagster lives in the optional `orchestration` extra (`backend/pyproject.toml`) and its own image (`deploy/dagster/Dockerfile`), so the API image does not grow.

## Why Dagster

The pipeline is a chain of data products: postings, then extracted fields, then embeddings, then views. Dagster models these products directly as **assets** with lineage, instead of modelling tasks.

| | Dagster | Airflow | Prefect | cron + `make pipeline` |
|---|---|---|---|---|
| Unit of work | asset (a table, file or view) with lineage | task in a DAG | task/flow | shell command |
| Data-quality gates | asset checks, blocking or warning | separate operators or tools | custom code | none |
| Partitions and backfills | built in (static, time, dynamic) | by run date | custom | manual |
| Run metadata (counts, durations) | per materialisation, in the UI | XCom/logs | artifacts | `ingest_runs` only |
| Local testing | `materialize()` / `execute_in_process()` in pytest | needs a scheduler setup | in-process flows | n/a |
| Ops footprint here | webserver, daemon, code server; Postgres reused | scheduler, webserver, metadata DB, executor | server or cloud | none |

Airflow is task-centred and heavier to run locally for one pipeline. Prefect fits flows well, but its checks and lineage would be custom code. Cron cannot retry one step, gate on data quality or show history. Dagster's asset checks and partitions match the two needs here: per-source reruns and blocking privacy checks.

## Asset graph

```mermaid
flowchart LR
  subgraph postings
    RAW["raw_postings<br/>partitions: arbeitsagentur, arbeitnow"] --> DED[deduplicated_postings]
    DED --> SCOPE[in_scope_postings]
    SCOPE --> EXT[extracted_fields]
    EXT --> SAL[salary_bluecard]
    SCOPE --> EMB[posting_embeddings]
    SAL --> INS[insights_views]
    EMB --> INS
  end
  subgraph snapshot
    SAL --> SNAP["postings_snapshot<br/>Parquet + DuckDB"]
    SNAP --> SQ[snapshot_insights]
    SNAP --> ICE["postings_iceberg<br/>Iceberg, SQLite catalog"]
  end
  subgraph maintenance
    EXP[posting_expiry]
  end
```

| Asset | Calls | Metadata |
|---|---|---|
| `raw_postings` | `ingest_source` with the polite, cached client; idempotent upsert | fetched, inserted, updated, unchanged per source, duration |
| `deduplicated_postings` | `run_dedup` (MinHash LSH, Jaccard ≥ 0.85) | duplicates marked |
| `in_scope_postings` | `prune_out_of_scope`, `retire_foreign_sites` | pruned, retired, active postings |
| `extracted_fields` | `run_rules_extraction`, then `run_extraction` (LLM) only if `PIPELINE_LLM_EXTRACTION=true` | rules extracted, LLM ok/failed, counts by method |
| `salary_bluecard` | `refresh_salaries` | checked, changed, Blue Card status counts |
| `posting_embeddings` | `run_embedding` | embedded, model, duration |
| `insights_views` | `refresh_insights` | views, active postings |
| `postings_snapshot` | Parquet + DuckDB export | rows, file, bytes, DuckDB rows over all snapshots |
| `snapshot_insights` | three DuckDB queries | result tables as Markdown |
| `postings_iceberg` | PyIceberg overwrite of today's rows | rows now, rows before this run (time travel), Iceberg snapshots |
| `posting_expiry` | `run_expiry` | checked, alive, gone, unknown, deactivated |

The LLM step is off by default, so a run needs no model. The `fixtures` ingest mode (`PIPELINE_INGEST_MODE=fixtures`) serves fictional API responses in-process (`jobscout/orchestration/fixtures.py`). The real fetchers, cleaning, upsert and dedup still run, but nothing reaches the network. `offline` replays the recorded HTTP cache, and `live` calls the APIs.

## Checks and severities

**Blocking** checks (severity ERROR) stop the assets downstream of the checked asset in the same run. **Warning** checks only report.

| Check | Asset | Severity | Rule |
|---|---|---|---|
| `posting_ids_unique` | raw_postings | blocking | IDs match `^(ba\|an)-[0-9a-f]{10}$`; IDs and (source, source_id) unique |
| `required_fields_present` | raw_postings | blocking | id, source, source_id, url, title, raw_text and content_hash not empty |
| `no_cv_text_stored` | raw_postings | blocking (privacy) | no CV/resume text column anywhere; LLM cache holds posting extractions only; opt-in `cv_profiles` hold only `CandidateProfile` keys and no contact data |
| `extractions_match_schema` | extracted_fields | blocking | every extracted row validates against the `JobPosting` Pydantic schema |
| `salary_fields_consistent` | salary_bluecard | blocking | min ≤ max, EUR, period year/month/hour, annual EUR 1,000 to 1,000,000 |
| `embeddings_complete` | posting_embeddings | blocking | every active posting has a vector of `EMBED_DIM` |
| `snapshot_ids_unique` | postings_snapshot | blocking | IDs in the latest Parquet file unique and well-formed |
| `snapshot_contacts_masked` | postings_snapshot | blocking (privacy) | no e-mail address or phone number in the snapshot text |
| `raw_postings_fresh` | raw_postings | warning | newest active posting seen less than 8 days ago (weekly schedule + 1 day) |
| `ingest_volume_stable` | raw_postings | warning | no source fetched less than 50% of its previous successful run |

The 50% volume threshold is a judgement, not fitted to data. Weekly volumes move with season and holidays. A halving points to an API change, a blocked client or an empty cache. The salary floor is low on purpose: mandatory internships pay a few hundred euros a month. Masking rules are in [Privacy rules](#privacy-rules) below and in `orchestration/masking.py`.

## Schedules, retries and alerts

| | Definition |
|---|---|
| `weekly_pipeline_schedule` | Mondays 03:00 Europe/Berlin. One run over both source partitions (partition-range tags, single-run backfill policy), then every downstream asset once. `run_key` = ISO week, so a week is never run twice by the schedule. |
| `daily_expiry_schedule` | daily 04:30 Europe/Berlin, `posting_expiry_job` (up to `PIPELINE_EXPIRY_LIMIT`, default 500, oldest-checked first) |
| Network retry | ingest, LLM extraction and expiry: `RetryPolicy(max_retries=3, delay=60, backoff=EXPONENTIAL, jitter=PLUS_MINUS)`, i.e. about 1, 3 and 7 minutes, each shifted by up to ±60 s of jitter. The HTTP client already retries single requests, so this covers outages. A retry repeats only that step, and every step is idempotent. |
| Embedding retry | `RetryPolicy(max_retries=2, delay=15, backoff=EXPONENTIAL)` |
| Concurrency | `max_concurrent_runs: 1`: two overlapping runs would both rewrite duplicate marks |
| `pipeline_run_failure_sensor` | logs a structured `pipeline_run_failed` event (job, run ID, failed steps) and increments `jobscout_pipeline_run_failures_total{job}` |

The Dagster code server exports Prometheus metrics on `:9465` (`orchestration/metrics.py`):
- the failure counter;
- `jobscout_pipeline_runs{job,status}`;
- `jobscout_pipeline_last_success_timestamp_seconds{job}`;
- `jobscout_pipeline_check_passed{check,asset,severity}`.

Grafana's JobScout dashboard has a "Pipeline (Dagster)" row with runs, failures, hours since the last success and check results. `deploy/prometheus/alerts.yml` adds `PipelineRunFailed`, `PipelineBlockingCheckFailed` and `PipelineStale` (no successful weekly run for 8 days). [When something fails](#when-something-fails) says what to do when one fires.

## Running it

**Local UI** (state in `.cache/dagster`, SQLite):
```bash
make dagster-validate     # the definitions load
make dagster-dev          # http://localhost:3070
```
`make dagster-dev` writes to the database in `DATABASE_URL` and starts the schedules. Set `PIPELINE_INGEST_MODE=fixtures` to try it without calling the APIs.

**Docker Compose** (profile `orchestration`): the code server (`dagster-code`), webserver and daemon use `jobscout-dagster:local`. Dagster's run, event and schedule storage is a separate `dagster` database in the Compose Postgres.
```bash
make dagster-up           # http://localhost:3070
make dagster-down
# try it on a separate database with fictional fixtures:
DAGSTER_PIPELINE_DATABASE_URL=postgresql+asyncpg://jobscout:jobscout@postgres:5432/jobscout_orch \
DAGSTER_PIPELINE_READONLY_DATABASE_URL=postgresql+asyncpg://jobscout_ro:jobscout_ro@postgres:5432/jobscout_orch \
PIPELINE_INGEST_MODE=fixtures make dagster-up
```
The separate database must exist (`CREATE DATABASE jobscout_orch`). The code server migrates it on start.

**Kubernetes (kind):** the official chart `dagster/dagster` 1.13.31 from `https://dagster-io.github.io/helm`, values in `deploy/k8s/dagster/values.yaml`.
```bash
make k8s-up               # the JobScout release (Postgres, app Secret)
make k8s-dagster-up       # DAGSTER_PIPELINE_DB=jobscout_orch to use a separate database
kubectl -n jobscout port-forward svc/dagster-dagster-webserver 3071:80   # http://localhost:3071
make k8s-dagster-down
```
| Piece | How |
|---|---|
| Webserver, daemon | official `dagster/dagster-celery-k8s:1.13.31` images from the chart, resource requests/limits set |
| User code | deployment `jobscout-pipeline` running `jobscout-dagster:local` (`dagster api grpc -m jobscout.orchestration.definitions`), loaded into kind and never pushed |
| Runs | `K8sRunLauncher`: one Kubernetes Job per run. `includeConfigInLaunchedRuns` gives run pods the same image, env, secrets, resources and snapshot volume. |
| Storage | external Postgres (`postgresql.enabled: false`): database `dagster` in the JobScout Postgres |
| Secrets | `deploy/k8s/dagster/up.sh` creates `dagster-postgresql-secret` and `jobscout-dagster-env` with `kubectl create secret` from the JobScout release's Secret. No secret is in a values file. |
| Snapshots | PVC `jobscout-dagster-snapshots` (kind local-path) mounted at `/app/.cache/snapshots` |
| Lint | `up.sh` runs `helm lint` on the pulled chart with these values before installing |

## When something fails

| Symptom | What to do |
|---|---|
| `raw_postings` failed after its retries | Open the run and read the step log: `ingestion failed for <source>` means the job API refused or timed out. Check `ingest_runs` (`stats->'http'`). Never rotate user agents or IPs to get around a block. Raise `HTTP_MIN_INTERVAL_S` and re-run that source only (see below). Rows fetched before the error are already stored. |
| A blocking check failed | The run stops below the checked asset, so search, insights and the snapshot keep the last good state. The check's metadata lists the failing IDs (never the values). Fix the data or the rule, then re-execute the run from the failed step in the UI ("Re-execute from failure"). |
| `snapshot_contacts_masked` failed | Treat it as a privacy incident. Delete the newest `snapshot_date=` folder and the DuckDB file, fix `masking.py` with a test for the missed pattern, and re-run `postings_snapshot`. |
| `raw_postings_fresh` / `ingest_volume_stable` warn | The schedule did not run (check the daemon: `docker compose logs dagster-daemon`) or a source returned much less than last week. Compare `by_source` in the last two `raw_postings` materialisations. |
| `PipelineStale` alert | The daemon is down or the schedule is stopped: Overview → Schedules in the UI. |

## Backfilling one source

Each source is a static partition of `raw_postings`. To re-ingest only Arbeitnow (for example after an Arbeitnow outage):
- **UI:** Assets → `raw_postings` → Materialize → partition `arbeitnow` (add downstream assets to refresh them), or launch `weekly_pipeline` with partition `arbeitnow`.
- **CLI (Compose):** queue the run on the running instance, and the daemon picks it up:
  ```bash
  docker compose -f deploy/docker-compose.yml --profile orchestration exec dagster-webserver \
    dagster job launch -w /opt/dagster/dagster_home/workspace.yaml -j weekly_pipeline \
    --tags '{"dagster/partition": "arbeitnow"}'
  ```

The upsert is idempotent, so unchanged postings only get `last_seen_at` refreshed.

## Privacy rules

- **CV text is never stored.** CVs are processed in memory. A user can opt in to keeping a structured profile (skills, years, languages, education level, cities) in `cv_profiles`. `no_cv_text_stored` proves this on every run: no column anywhere is named like a CV or resume text field, the LLM cache holds only posting extractions, and stored profiles contain only `CandidateProfile` keys and no e-mail address or phone number.
- **Snapshot text is masked.** Before posting text leaves Postgres, `mask_contacts` replaces e-mail addresses with `[email]` and phone numbers with `[phone]`. A phone number must start with `+` or `0` (or follow a "Tel"/"Phone" label) and contain 7 to 15 digits, so salaries, dates, postcodes and reference numbers stay readable. `snapshot_contacts_masked` re-scans the written file with the same patterns. Tests cover seven phone formats and five kinds of number that must not be masked.
- **Check metadata holds IDs, never values,** so a failing check cannot copy contact details into Dagster's run storage.

## Snapshot and DuckDB

`postings_snapshot` writes the active, de-duplicated, extracted postings to `<SNAPSHOT_DIR>/parquet/snapshot_date=YYYY-MM-DD/postings.parquet` (zstd). The folders are Hive-style partitions by snapshot date. The posting text is replaced by `text_masked`. Rewriting a date replaces the file atomically. The asset then rebuilds `jobscout.duckdb` from all snapshots. Postgres stays the row store that serves the app. The columnar copy is for scans and history across snapshots, which Postgres keeps only as the latest state.

`snapshot_insights` runs three queries (`assets/snapshot.py`). Cells smaller than `min_cell_size` (default 5, as in the public insights) are hidden.
1. `english_friendly_by_city`: share of English-friendly postings per city per snapshot.
2. `salary_transparency_by_source`: share stating a salary, and the median annual minimum, per source per snapshot.
3. `top_skills_english_friendly`: most requested skills among English-friendly postings in the latest snapshot.

`postings_iceberg` writes the same rows to an Apache Iceberg table through PyIceberg. It uses a local SQL catalog (SQLite) and a warehouse folder. Each run overwrites today's rows (one delete and one append commit), then reads the table as it was before the run (time travel by snapshot ID).

## Results recorded on 2026-11-20

All runs used the fictional fixtures (`PIPELINE_INGEST_MODE=fixtures`, 6 postings, 3 per source) on a separate database, `jobscout_orch`. The application data was not touched. Numbers are copied from the Dagster run metadata.

**Tests** (`make test`, in-process, test Postgres): the orchestration tests materialise the job with good fixtures and assert the counts and that all 10 checks pass. A second run is idempotent. Bad fixtures each fail their blocking check and stop the assets below it: a malformed posting ID, an invalid extraction, duplicate IDs in the snapshot, an unmasked e-mail address in the snapshot, and CV text in the database. 433 backend tests passed, coverage 90.74%.

**Docker Compose** (`make dagster-up`; runs launched through the webserver and executed via the daemon's queue):

| Run | Result | Details |
|---|---|---|
| weekly_pipeline, both sources | success, 33.8 s | fetched 6, inserted 6, duplicates marked 1, active 5, rules-extracted 5, embedded 5 (Ollama `bge-m3`), Blue Card: 2 meet the standard threshold, 3 state no salary; snapshot 5 rows (13,622 bytes); 10 of 10 checks passed |
| weekly_pipeline with a malformed posting ID inserted | failed after 6.6 s at `posting_ids_unique` | no downstream asset ran; the sensor logged `pipeline_run_failed`; `jobscout_pipeline_run_failures_total{job="weekly_pipeline"}` = 1; Prometheus scraped the exporter |
| posting_expiry_job | success, 2.8 s | checked 5, alive 5 |
| weekly_pipeline, partition `arbeitnow` only | success, 33.8 s | fetched 3, unchanged 3 |
| weekly_pipeline rerun | success, 33.3 s | inserted 0, unchanged 6, embedded 0 |
| weekly_pipeline with the Iceberg asset | success, 35.6 s | Iceberg rows written 5, rows now 5 |

**kind** (`make k8s-dagster-up` with `DAGSTER_PIPELINE_DB=jobscout_orch`; runs launched through the webserver, each executed as a Kubernetes Job by the `K8sRunLauncher`):

| Run | Result | Details |
|---|---|---|
| weekly_pipeline, both sources | success, 56.5 s (Job completed in 63 s) | fetched 6, inserted 6, duplicates marked 1, rules-extracted 5, embedded 5 (Ollama on the host via `host.docker.internal`), snapshot 5 rows, Iceberg rows 5; 10 of 10 checks passed |
| weekly_pipeline rerun | success, 65.3 s | inserted 0, unchanged 6. The Iceberg time-travel read found the 5 rows written by the previous run's pod, so the snapshot volume persists across run pods. 10 of 10 checks passed. |

`helm lint` of chart 1.13.31 with `deploy/k8s/dagster/values.yaml`: 1 chart linted, 0 failed. The first install found two problems, both fixed in the repo: the user-code Service name clashed with the app Service (deployment renamed to `jobscout-pipeline`), and `runAsNonRoot` needs a numeric image user (`USER 10001:10001`).

## Limits

- The demo runs use 6 fictional postings. Run times on the real corpus were not measured.
- Snapshots on kind live on a single-node local-path volume. A shared deployment needs object storage (and an object-store Iceberg catalog).
- The metrics exporter runs in the code server only. On kind it is not scraped, because there is no Prometheus in that cluster.
