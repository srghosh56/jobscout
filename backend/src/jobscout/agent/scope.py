"""Scope check: decides by meaning whether a question belongs to JobScout.

The question is embedded and compared with example questions in
config/scope_examples.yaml. If its closest example is off-topic, it gets a fixed
refusal and the LLM never runs. If the embedding model fails, the question goes to
the agent, so an outage never blocks real users.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import yaml

from jobscout.llm import embed_model_version, get_embeddings
from jobscout.logging import get_logger
from jobscout.settings import CONFIG_DIR

log = get_logger(__name__)
EXAMPLES_FILE = CONFIG_DIR / "scope_examples.yaml"

Matrix = npt.NDArray[np.float32]


@dataclass(frozen=True)
class ScopeDecision:
    in_scope: bool
    on_topic_similarity: float | None = None
    off_topic_similarity: float | None = None


REFUSALS = {
    "en": "I can't help with that. I can answer questions about skilled jobs in Germany, "
    "the postings, the EU Blue Card and CV matching.",
    "de": "Dabei kann ich nicht helfen. Ich beantworte Fragen zu Fachkräfte-Stellen in "
    "Deutschland, den Anzeigen, der Blauen Karte EU und zum CV-Abgleich.",
}

_cache: dict[str, tuple[Matrix, Matrix]] = {}
_lock = asyncio.Lock()


def refusal_message(language: str) -> str:
    """Returns the refusal in the user's language, falling back to English."""
    return REFUSALS.get(language, REFUSALS["en"])


def _unit(vectors: list[list[float]]) -> Matrix:
    """Scales vectors to length 1, so a dot product equals cosine similarity."""
    matrix = np.asarray(vectors, dtype=np.float32)
    norms: Matrix = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / norms


def classify(question: Matrix, on_topic: Matrix, off_topic: Matrix) -> ScopeDecision:
    """In scope when the closest on-topic example is at least as close as the off-topic one."""
    on_similarity = float((on_topic @ question[0]).max())
    off_similarity = float((off_topic @ question[0]).max())
    return ScopeDecision(on_similarity >= off_similarity, on_similarity, off_similarity)


async def _example_vectors() -> tuple[Matrix, Matrix]:
    """Embeds the examples once per model and process; a failure is retried on the next call."""
    model = embed_model_version()
    async with _lock:
        if model not in _cache:
            examples = yaml.safe_load(EXAMPLES_FILE.read_text(encoding="utf-8"))
            on_topic, off_topic = examples["on_topic"], examples["off_topic"]
            vectors = await get_embeddings().aembed_documents([*on_topic, *off_topic])
            _cache[model] = (_unit(vectors[: len(on_topic)]), _unit(vectors[len(on_topic) :]))
    return _cache[model]


async def check_scope(question: str) -> ScopeDecision:
    try:
        on_topic, off_topic = await _example_vectors()
        vector = await get_embeddings().aembed_documents([question])
        return classify(_unit(vector), on_topic, off_topic)
    except Exception as exc:
        log.warning("scope_check_failed", error=type(exc).__name__)
        return ScopeDecision(in_scope=True)
