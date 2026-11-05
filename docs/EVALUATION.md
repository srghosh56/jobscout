# Evaluation

**Read this first.** All numbers below were produced by scripts in this repo on 2026-11-02 with **local models**: Mistral 7B (Q4, Ollama) as chat model, extractor and judge, and bge-m3 for embeddings, on an Apple M4 Pro. They do **not** describe what the hosted Mistral API or Gemini would score ("not measured"). Expected answers in the golden set (`eval/golden.yaml`, version 1) are generated from reference SQL and rule-based predicates, and the LLM-judge scores are **uncalibrated**.

Reproduce: `make dataset-stats`, `make eval-full`, `make eval-record` (smoke), `make retrieval-bench`, `make ablations`, then `make eval-report` to regenerate the tables between the markers.

<!-- BEGIN GENERATED (make eval-report) -->
### Dataset (`make dataset-stats`)
| Measure | Value |
|---|---|
| Unique postings (active) | 3003 (3003) |
| Near-duplicates detected (MinHash) | 291 |
| By source | arbeitnow: 1626, arbeitsagentur: 1377 |
| By posting language | de: 1967, en: 1036 |
| LLM-extracted / rules-extracted / failed | 228 / 2775 / 3 |
| Embedded | 3003 |

Rules vs LLM field agreement on the 228 LLM-extracted postings:

| Field | Agreement |
|---|---|
| english_sufficient | 84.6% |
| german_level | 75.0% |
| seniority | 74.6% |
| remote_policy | 85.1% |
| visa_sponsorship | 95.6% |
| relocation_support | 98.7% |
| salary_stated | 98.7% |
| min_years_experience | 42.5% |

### Agent evaluation (`make eval-full`, `make eval-record`)
| Metric | full_v1 (ollama:mistral:latest, n=80) | smoke (synthetic corpus, ollama:mistral:latest, n=20) |
|---|---|---|
| Tool-selection accuracy | 70.0% | 70.0% |
| SQL execution accuracy (result-set match) | 60.0% | 50.0% |
| Recall@5 (agent's first search) | 0.32 | 1 |
| MRR (agent's first search) | 0.61 | 1 |
| Citation precision (first draft) | 67.4% | 72.7% |
| Refusal recall on out-of-scope/adversarial (judge) | 0.0% | 0.0% |
| Over-refusal on in-scope questions (judge) | 0.0% | 0.0% |
| Refusal decision correct, all questions (judge) | 91.2% | 80.0% |
| Judge faithfulness (1-5, uncalibrated) | 4.49 | 4.55 |
| Judge relevance (1-5, uncalibrated) | 4.61 | 4.45 |
| Latency p50 (s) | 35.1 | 16.1 |
| Latency p95 (s) | 124.5 | 43.1 |
| Tokens per question | 5922 | 3985 |
| API cost per question (EUR) | 0 | 0 |

Tool-selection accuracy by category (full_v1 (ollama:mistral:latest, n=80)):

| Category | n | Accuracy |
|---|---|---|
| adversarial | 3 | 100.0% |
| cv | 8 | 12.5% |
| multi_step | 15 | 0.0% |
| out_of_scope | 4 | 75.0% |
| semantic | 25 | 96.0% |
| sql | 25 | 100.0% |

### Retrieval ablation (`make retrieval-bench`, 25 semantic questions)
| Mode | Recall@5 | MRR | nDCG@10 | p50 latency (s) | p95 latency (s) |
|---|---|---|---|---|---|
| fts | 0.092 | 0.261 | 0.16 | 0.164 | 0.945 |
| dense | 0.354 | 0.743 | 0.518 | 0.277 | 0.768 |
| hybrid | 0.239 | 0.509 | 0.366 | 0.274 | 0.321 |
| hybrid_rerank | 0.441 | 0.835 | 0.634 | 0.746 | 1.043 |

### Plain single-shot RAG vs agent (`make ablations`, golden semantic + multi-step)
| Metric | Single-shot RAG | Agent |
|---|---|---|
| Judge faithfulness | 4.95 | 4.62 |
| Judge relevance | 4.9 | 4.65 |
| Citation precision | 1 | 0.879 |
| Recall@5 | 0.441 | 0.32 |
| MRR | 0.835 | 0.61 |
| Latency p50 (s) | 27.3 | 71.4 |
| Latency p95 (s) | 33.5 | 176.2 |

Architecture/model ablation: not measured: GEMINI_API_KEY not set (single vs supervisor+critic and Mistral vs Gemini need it)

### Judge calibration
not measured.
<!-- END GENERATED -->

## What the numbers say (plain English)

**Data.** Every posting has an extraction, but most were produced by the deterministic rules and only a minority by the LLM (local throughput limited the LLM pass). On postings with both, the rules agree with the LLM on most safety-relevant fields because those fields are rule-derived in both paths. Agreement on `min_years_experience` and seniority is lower: that's where the LLM adds information and where rule-only rows are weaker.

**Where the agent is good.**
- **Choosing the tool for single-intent questions:** SQL questions always went to `sql_query`, and semantic questions almost always to `search_postings` (see the per-category table).
- **Safety under adversarial and off-topic prompts:** no harmful tool action (the one `DROP TABLE` request never reached the database), no system-prompt leak detected, no over-refusal of in-scope questions.
- **Staying grounded when it answers:** judge faithfulness/relevance are high on the 1–5 scale. Treat this as optimistic until calibrated: the judge is the same model family as the agent (self-preference bias).

**What is weak or got worse (honest list).**
- **Explicit refusals: 0 of 7.** The local model never says "I can't help with that"; it deflects ("no relevant postings were found…") or asks a clarifying question (weather). The judge correctly doesn't count deflection as a refusal. The aggregate "refusal decision correct" looks high only because in-scope questions are (correctly) never refused: don't quote it without the recall number.
- **Multi-step questions:** tool-selection accuracy is the worst category. The local router usually stops after the first tool (e.g. searches but never calls `bluecard_check`).
- **CV questions in chat:** the router rarely builds a profile and calls `match_cv`; it searches instead. The CV page calls `match_cv` directly, so the feature works there, but not through chat.
- **SQL execution accuracy** is limited by wrong SQL semantics (operator precedence, extra conditions, missing columns), even though the guard and the LIMIT repair work.
- **Citation precision on first drafts:** the composer still invents IDs on some answers. The checker catches them (regeneration or "unverified" marking), so final answers are safer than this number suggests, but it shows the model's tendency.
- **Recall@5 of the agent's first search** is far below MRR: the first relevant posting is often near the top, but the agent's query (with its own filters) rarely retrieves *all* relevant postings in five results. Some relevant sets have 10–25 postings, which caps recall@5 below 1 by construction.
- **Latency** is minutes at p95 on local hardware; parts of the full run overlapped with a Docker build and a model download on the same machine, which inflated latency. Don't quote latency as a product number.

**Smoke set (CI).** The first recording failed the a-priori thresholds (SQL execution accuracy near zero: the model omitted LIMIT and the composer turned the guard error into "no postings"). After a fix to the LIMIT handling the re-recording improved; CI thresholds in `eval/smoke.yaml` are **regression gates set just below this recorded baseline**, not quality targets.

**Retrieval ablation (25 semantic questions).**
- **Hybrid + cross-encoder rerank is the best mode** on every quality metric (recall@5 0.441, MRR 0.835, nDCG@10 0.634) for about 0.5 s extra median latency. This is the configuration the product uses.
- **What got worse: plain hybrid (RRF without rerank) is *worse* than dense alone** (recall@5 0.239 vs 0.354; MRR 0.509 vs 0.743). Full-text search is very weak here (recall@5 0.092) even though the labels favour lexical matches. Likely reasons: OR-ed query terms with `ts_rank_cd` (no IDF) reward generic words, and English queries against German ads. RRF gives the weak list equal weight, so it pulls good dense results down. The reranker recovers this. Next step: weighted RRF or BM25 (`pg_search`), measured with the same bench.

**Plain single-shot RAG vs the agent (40 semantic + multi-step questions).**
- **What got worse: the agent loses to a fixed retrieve-then-answer pipeline on these questions.** Single-shot RAG (hybrid+rerank top-8 → one composer call) scored higher judge faithfulness/relevance (4.95/4.90 vs 4.62/4.65, uncalibrated), perfect first-draft citation precision (1.0 vs 0.879), better retrieval (recall@5 0.441 vs 0.32; MRR 0.835 vs 0.61) and much lower latency (p50 27 s vs 71 s). With a 7B router, the agent's own queries and filters are worse than simply searching with the user's question, and the extra calls cost time.
- **Where the agent still earns its keep:** questions that need SQL (counts, shares, rankings), where single-shot RAG can't answer, and tool combinations (Blue Card checks, comparisons). That's exactly where the multi-step weakness lies, though.
- **Implication:** route search-intent questions to the deterministic pipeline and reserve the agent for data and multi-tool questions (the supervisor variant does this structurally; not measured yet), and re-run this ablation with a hosted model before deciding.

## Caveats
- Semantic relevance labels come from regex predicates over title/text (`label_method: reference_predicate`). They favour lexical matches, so they flatter full-text search, and they miss relevant postings that use other words.
- The judge is uncalibrated: **judge agreement / Cohen's kappa: not measured.**
- API cost per question is 0 EUR for local models (hardware and electricity not counted); hosted prices are null in `config/pricing.yaml`, so hosted cost is **not measured**.
- Supervisor + critic vs single agent and Mistral vs Gemini: **not measured** (needs a Gemini key).

## Metrics per golden-set version
| Golden version | Results file | Notes |
|---|---|---|
| 1 | `eval/results/full_v1.json` | 80 questions, local Mistral 7B |
