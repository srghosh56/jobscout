"""Decides whether a question can take the single-shot path: one search, one answer.

The question is embedded and compared with the examples in config/route_examples.yaml.
It takes the single-shot path only when its closest search-only example beats its closest
needs-agent example by MARGIN, because a wrong agent choice costs time and a wrong
single-shot choice costs a correct answer. If embedding fails, the question goes to the agent.
"""

from __future__ import annotations

import asyncio

import yaml

from jobscout.agent.scope import Matrix, _unit
from jobscout.llm import embed_model_version, get_embeddings
from jobscout.logging import get_logger
from jobscout.settings import CONFIG_DIR

log = get_logger(__name__)
EXAMPLES_FILE = CONFIG_DIR / "route_examples.yaml"
MARGIN = 0.02

_cache: dict[str, tuple[Matrix, Matrix]] = {}
_lock = asyncio.Lock()


def prefers_search(question: Matrix, search_only: Matrix, needs_agent: Matrix) -> bool:
    search_similarity = float((search_only @ question[0]).max())
    agent_similarity = float((needs_agent @ question[0]).max())
    return search_similarity - agent_similarity > MARGIN


async def _example_vectors() -> tuple[Matrix, Matrix]:
    """Embeds the examples once per model and process; a failure is retried on the next call."""
    model = embed_model_version()
    async with _lock:
        if model not in _cache:
            examples = yaml.safe_load(EXAMPLES_FILE.read_text(encoding="utf-8"))
            search_only, needs_agent = examples["search_only"], examples["needs_agent"]
            vectors = await get_embeddings().aembed_documents([*search_only, *needs_agent])
            split = len(search_only)
            _cache[model] = (_unit(vectors[:split]), _unit(vectors[split:]))
    return _cache[model]


async def is_search_only(question: str) -> bool:
    try:
        search_only, needs_agent = await _example_vectors()
        vector = await get_embeddings().aembed_documents([question])
        return prefers_search(_unit(vector), search_only, needs_agent)
    except Exception as exc:
        log.warning("route_check_failed", error=type(exc).__name__)
        return False
