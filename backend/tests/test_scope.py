"""Scope decision logic. Vectors are chosen by hand, so these tests need no model."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from jobscout.agent import scope
from jobscout.agent.scope import check_scope, classify, refusal_message

ON_TOPIC = np.array([[1, 0, 0]], dtype=np.float32)
OFF_TOPIC = np.array([[0, 1, 0]], dtype=np.float32)


def test_question_near_a_job_example_is_in_scope() -> None:
    decision = classify(np.array([[0.9, 0.1, 0]], dtype=np.float32), ON_TOPIC, OFF_TOPIC)
    assert decision.in_scope


def test_question_near_an_off_topic_example_is_refused() -> None:
    decision = classify(np.array([[0.1, 0.9, 0]], dtype=np.float32), ON_TOPIC, OFF_TOPIC)
    assert not decision.in_scope


def test_equal_distance_lets_the_question_through() -> None:
    decision = classify(np.array([[0.5, 0.5, 0]], dtype=np.float32), ON_TOPIC, OFF_TOPIC)
    assert decision.in_scope


def test_refusal_uses_the_users_language() -> None:
    assert refusal_message("de").startswith("Dabei kann ich nicht helfen")
    assert refusal_message("en").startswith("I can't help with that")


def test_unknown_language_falls_back_to_english() -> None:
    assert refusal_message("fr") == refusal_message("en")


def test_example_file_has_both_groups_and_no_duplicates() -> None:
    import yaml

    examples = yaml.safe_load(scope.EXAMPLES_FILE.read_text(encoding="utf-8"))
    on_topic, off_topic = examples["on_topic"], examples["off_topic"]
    assert on_topic and off_topic
    assert not set(on_topic) & set(off_topic)


async def test_embedding_failure_lets_the_question_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        async def aembed_documents(self, _texts: list[str]) -> Any:
            raise ConnectionError("model is down")

    monkeypatch.setattr(scope, "get_embeddings", Broken)
    monkeypatch.setattr(scope, "embed_model_version", lambda: "broken-model")
    assert (await check_scope("How many jobs are in Berlin?")).in_scope
