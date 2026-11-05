"""Prometheus metrics. Label values are always from small fixed sets (bounded
cardinality): never posting IDs, user input, API keys or raw paths."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80)

REQUEST_LATENCY = Histogram(
    "jobscout_http_request_duration_seconds",
    "HTTP request latency",
    ["route", "method", "status_class"],
    buckets=LATENCY_BUCKETS,
)
TIME_TO_FIRST_TOKEN = Histogram(
    "jobscout_chat_time_to_first_token_seconds",
    "Time from chat request to first streamed answer token",
    buckets=LATENCY_BUCKETS,
)
LLM_TOKENS = Counter("jobscout_llm_tokens_total", "LLM tokens", ["role", "direction"])
LLM_COST = Counter(
    "jobscout_llm_cost_eur_total", "Estimated LLM cost in EUR (0 for local models)", ["role"]
)
LLM_CALLS = Counter("jobscout_llm_calls_total", "LLM calls", ["role", "outcome"])
TOOL_CALLS = Counter("jobscout_tool_calls_total", "Agent tool calls", ["tool", "outcome"])
SQL_GUARD_REJECTIONS = Counter(
    "jobscout_sql_guard_rejections_total", "SQL guard rejections", ["reason"]
)
RETRIEVAL_LATENCY = Histogram(
    "jobscout_retrieval_duration_seconds",
    "Hybrid retrieval latency",
    ["mode"],
    buckets=LATENCY_BUCKETS,
)
CITATION_CHECKS = Counter("jobscout_citation_checks_total", "Citation check outcomes", ["outcome"])
RATE_LIMITED = Counter(
    "jobscout_rate_limited_total", "Requests rejected by the rate limiter", ["tier"]
)
INGEST_POSTINGS = Gauge(
    "jobscout_ingest_last_run_postings", "Postings in the last ingest run", ["source", "kind"]
)
ACTIVE_POSTINGS = Gauge("jobscout_active_postings", "Active, non-duplicate postings")
FEEDBACK = Counter("jobscout_feedback_total", "Answer feedback", ["rating"])
TOOL_CALLS_RECOVERED = Counter(
    "jobscout_tool_calls_recovered_total",
    "Tool calls recovered from text (model did not use native calling)",
)

SCOPE_REFUSALS = Counter("jobscout_scope_refusals_total", "Questions refused by the scope check")
SINGLE_SHOT_QUESTIONS = Counter(
    "jobscout_single_shot_questions_total",
    "Questions answered with one search, without the router",
)
