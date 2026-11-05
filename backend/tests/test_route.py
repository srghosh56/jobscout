"""Single-shot routing. Vectors are chosen by hand, so these tests need no model."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from jobscout.agent import route
from jobscout.agent.graph import build_graph, turn_input
from jobscout.agent.route import is_search_only, prefers_search
from jobscout.llm import FakeChatModel

SEARCH = np.array([[1, 0, 0]], dtype=np.float32)
AGENT = np.array([[0, 1, 0]], dtype=np.float32)


def test_question_near_a_search_example_takes_the_short_path() -> None:
    assert prefers_search(np.array([[0.9, 0.1, 0]], dtype=np.float32), SEARCH, AGENT)


def test_question_near_an_agent_example_stays_with_the_agent() -> None:
    assert not prefers_search(np.array([[0.1, 0.9, 0]], dtype=np.float32), SEARCH, AGENT)


def test_a_tie_stays_with_the_agent() -> None:
    assert not prefers_search(np.array([[1, 1, 0]], dtype=np.float32), SEARCH, AGENT)


async def test_embedding_failure_stays_with_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail() -> Any:
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(route, "_example_vectors", fail)
    assert not await is_search_only("MLOps engineer jobs")


def _graph(monkeypatch: pytest.MonkeyPatch, search_only: bool, hits: list[Any]) -> tuple[Any, Any]:
    calls: list[str] = []

    async def decide(_question: str) -> bool:
        return search_only

    async def tool(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], float]:
        calls.append(f"tool:{name}:{args.get('query')}")
        return {"query": args.get("query"), "results": hits}, 0.0

    def respond(_messages: list[Any], **_kwargs: Any) -> AIMessage:
        calls.append("llm")
        return AIMessage(content="No matching postings.")

    monkeypatch.setattr("jobscout.agent.graph.is_search_only", decide)
    monkeypatch.setattr("jobscout.agent.graph.run_tool", tool)
    graph = build_graph(FakeChatModel(responder=respond), InMemorySaver(), single_shot=True)
    return graph, calls


async def test_a_search_question_skips_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, calls = _graph(monkeypatch, True, [{"id": "an-1234567890"}])
    config: RunnableConfig = {"configurable": {"thread_id": "short"}}
    state = await graph.ainvoke(turn_input("MLOps engineer jobs"), config)
    assert calls == ["tool:search_postings:MLOps engineer jobs", "llm"]
    assert state["answer"] == "No matching postings."


async def test_an_empty_search_goes_back_to_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, calls = _graph(monkeypatch, True, [])
    config: RunnableConfig = {"configurable": {"thread_id": "empty"}}
    await graph.ainvoke(turn_input("Lighthouse keeper jobs"), config)
    assert calls == ["tool:search_postings:Lighthouse keeper jobs", "llm", "llm"]


async def test_other_questions_go_to_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, calls = _graph(monkeypatch, False, [])
    config: RunnableConfig = {"configurable": {"thread_id": "router"}}
    await graph.ainvoke(turn_input("How many jobs are in Berlin?"), config)
    assert calls[0] == "llm"


async def test_a_follow_up_question_goes_to_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, calls = _graph(monkeypatch, True, [{"id": "an-1234567890"}])
    config: RunnableConfig = {"configurable": {"thread_id": "follow-up"}}
    await graph.ainvoke(turn_input("MLOps engineer jobs"), config)
    calls.clear()
    await graph.ainvoke(turn_input("Rust developer jobs"), config)
    assert not any(c.startswith("tool:") for c in calls)
