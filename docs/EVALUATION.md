# Evaluation

**Read this first.** The `full_v2` numbers were produced on 2026-11-06 by scripts in this repo with **local models** (Mistral 7B as the agent, Llama 3.2 as the judge) on the human-reviewed golden set, version 2. The `full_v1` column used an unreviewed golden set, older data, a judge that marked its own answers and an earlier recall formula, so it is not directly comparable.

**Recall@5 definition.** Hits in the top 5 divided by the smaller of 5 and the number of relevant postings, so a perfect top 5 scores 1.0 however long the relevant list is.

Reproduce: `make dataset-stats`, `make eval-full`, `make eval-record` (smoke), `make retrieval-bench`, `make ablations`, then `make eval-report` to regenerate the tables between the markers.

<!-- BEGIN GENERATED (make eval-report) -->
### Dataset (`make dataset-stats`)
| Measure | Value |
|---|---|
| Unique postings (active) | 3005 (2656) |
| Near-duplicates detected (MinHash) | 289 |
| By source | arbeitnow: 1628, arbeitsagentur: 1377 |
| By posting language | de: 1967, en: 1038 |
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
| salary_stated | 100.0% |
| min_years_experience | 42.5% |

### Agent evaluation (`make eval-full`, `make eval-record`)
| Metric | full_v1 (ollama:mistral:latest, n=80) | full_v2 (ollama:mistral:latest, n=80) | smoke (synthetic corpus, ollama:mistral:latest, n=20) |
|---|---|---|---|
| Tool-selection accuracy | 70.0% | 72.5% | 80.0% |
| SQL execution accuracy (result-set match) | 60.0% | 60.0% | 100.0% |
| Recall@5 (agent's first search) | 0.32 | 0.648 | 1 |
| MRR (agent's first search) | 0.61 | 0.908 | 1 |
| Citation precision (first draft) | 67.4% | 70.1% | 71.4% |
| Refusal recall on out-of-scope/adversarial (judge) | 0.0% | 85.7% | 0.0% |
| Over-refusal on in-scope questions (judge) | 0.0% | 4.1% | 0.0% |
| Refusal decision correct, all questions (judge) | 91.2% | 95.0% | 80.0% |
| Judge faithfulness (1-5, uncalibrated) | 4.49 | 3.77 | 3.8 |
| Judge relevance (1-5, uncalibrated) | 4.61 | 4.25 | 4.95 |
| Latency p50 (s) | 35.1 | 34.2 | 0 |
| Latency p95 (s) | 124.5 | 117.4 | 0 |
| Tokens per question | 5922 | 6078 | 4723 |
| API cost per question (EUR) | 0 | 0 | 0 |

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
| fts | 0.17 | 0.291 | 0.183 | 0.075 | 0.159 |
| dense | 0.511 | 0.777 | 0.554 | 0.259 | 0.31 |
| hybrid | 0.386 | 0.55 | 0.403 | 0.264 | 0.278 |
| hybrid_rerank | 0.669 | 0.913 | 0.696 | 0.732 | 0.803 |

### Plain single-shot RAG vs agent (`make ablations`, golden semantic + multi-step)
| Metric | Single-shot RAG | Agent |
|---|---|---|
| Judge faithfulness | 3.4 | 3.83 |
| Judge relevance | 4.53 | 4.65 |
| Citation precision | 0.987 | 0.85 |
| Recall@5 | 0.669 | 0.648 |
| MRR | 0.913 | 0.908 |
| Latency p50 (s) | 27.9 | 42.5 |
| Latency p95 (s) | 40.5 | 133 |

Architecture/model ablation: not measured: GEMINI_API_KEY not set (single vs supervisor+critic and Mistral vs Gemini need it)

### Judge calibration
| n labelled | Agreement | Cohen's kappa |
|---|---|---|
| 20 | 75.0% | 0.49 |
<!-- END GENERATED -->

## What the numbers say (plain English)

**Data.** Every posting has an extraction, but most come from the deterministic rules and only 228 from the LLM. Rule-based salary extraction was rewritten after review found phone numbers, perks and postcodes read as pay. It now favours precision, so some real salaries show as "not stated". Salary agreement between rules and LLM is meaningless now, because rules decide salary in both paths.

**Where the agent is good.**
- **Single-intent questions:** all SQL and all search questions go to the right tool.
- **Finding a relevant job:** MRR is 0.91, so a correct job is usually at or near the top.
- **Out-of-scope requests:** 6 of 7 were refused. `DROP TABLE` was not clearly refused, but it can't run: the SQL guard and read-only role block it.
- **Behaviour after the guardrails:** on the same questions, German questions answered in another language fell from 7 to 0, leaked internal wording from 13 answers to 0, and invalid search calls from 12 to 0.

**What is weak or got worse (honest list).**
- **Multi-step questions: 0 of 15** used all the required tools. The local model usually calls one tool and stops.
- **CV questions in chat: 1 of 8** used the required tool. The CV page calls `match_cv` directly, so the feature works there.
- **SQL execution accuracy stayed at 60%.** The guardrails stopped bad behaviour but did not make the model's SQL correct. One question improved and another got worse. A failed query now gets one guided retry, but the model often fails again.
- **What got worse: answers are slower** (median 28 s to 34 s), because some answers get a rewrite or a retry.
- **What got worse: 3 of 73 in-scope questions counted as refused**, up from 1. They are not real refusals: the SQL failed twice and the agent said it could not get the data, which the judge reads as refusing.
- **The answer checks are limited.** They catch numbers of 1,000 or more and SQL on its own line. multi-08 still showed an invented "90%" and invented rows, and sql-21 still said no jobs were found after its query failed.
- **Citation precision on first drafts** is 70%. The citation check catches invalid IDs before the answer is shown.

**Judge calibration.** On 20 hand-labelled answers, Llama 3.2 agreed with the reviewer on 75% (Cohen's kappa 0.49, moderate). Mistral marking its own answers agreed on 65% (0.34), and every disagreement was too generous. Llama made 5 mistakes: 3 too strict, 2 too generous. The answers differed between the two rounds and the sample is small, so treat this as a rough comparison. Judge scores are indicative only. The judge never sees the correct answer, so exact-match SQL accuracy is the stronger evidence.

**Smoke set (CI).** 20 questions on a synthetic corpus, replayed from recorded model answers, so CI needs no keys. It was re-recorded for the final agent.

**Retrieval ablation (25 semantic questions).**
- **Hybrid + cross-encoder rerank is the best mode** on every quality metric (recall@5 0.669, MRR 0.913, nDCG@10 0.696) for about 0.5 s extra median latency.
- **What got worse: plain hybrid (RRF without rerank) is worse than dense alone** (recall@5 0.386 vs 0.511). Full-text search is weak here (0.170), so fusing it in hurts until the reranker reorders the results.

**Plain single-shot RAG vs the agent (40 semantic + multi-step questions).**
- **Finding jobs is tied:** MRR is 0.913 vs 0.908, because plain search questions now use the same retrieval.
- **RAG is faster and has cleaner citations:** p95 40.5 s vs 133 s, and citation precision 98.7% vs 85.0%.
- **The judge rated the agent as more faithful** (3.83 vs 3.40). This is indicative only, given the moderate calibration.
- **Implication:** search-only questions take the single-shot path in the product. The agent is only justified for multi-tool questions, which is where this local model fails.

## Caveats
- Semantic relevance labels come from reviewed regex rules over title and text. They favour lexical matches and miss relevant postings that use other words.
- The golden set was reviewed once by one person, and some guardrail wording was written after reading these questions, so results may be slightly optimistic.
- Judge calibration uses 20 answers, and the reviewer saw the author's checks and opinions while labelling, so it is not fully independent.
- API cost per question is 0 EUR for local models (hardware and electricity not counted); hosted cost is **not measured**.
- Supervisor + critic vs single agent and Mistral vs Gemini: **not measured** (needs a Gemini key).

## Metrics per golden-set version
| Golden version | Results file | Notes |
|---|---|---|
| 1 | `eval/results/full_v1.json` | 80 questions, unreviewed, local Mistral 7B |
| 2 | `eval/results/full_v2.json` | 80 questions, human-reviewed, Mistral 7B agent, Llama 3.2 judge |
