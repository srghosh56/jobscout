"""Deterministic tests of the supervisor graph with a scripted fake model."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda

from jobscout.agent.supervisor import SupervisorAgent
from jobscout.llm import FakeChatModel
from tests.test_agent import call

pytestmark = pytest.mark.db


class ScriptedSupervisorModel(FakeChatModel):
    """route + critic verdicts via structured output; router/composer via responder."""

    structured: list[dict[str, Any]] = []  # noqa: RUF012 - pydantic field

    def with_structured_output(self, schema: Any, **kw: Any) -> RunnableLambda[Any, Any]:  # type: ignore[override]
        def run(_m: Any) -> Any:
            out = self.structured.pop(0)
            return {"raw": AIMessage(json.dumps(out)), "parsed": out, "parsing_error": None}

        return RunnableLambda(run)


def responder(steps: list[list[dict[str, Any]]], answers: list[str]) -> Any:
    def r(msgs: Sequence[BaseMessage]) -> AIMessage:
        first = str(msgs[0].content)
        if first.startswith("You are JobScout, an assistant"):
            return AIMessage(content="", tool_calls=steps.pop(0)) if steps else AIMessage("DONE")
        return AIMessage(answers.pop(0) if answers else "no answer")

    return r


async def test_routes_to_data_specialist_and_critic_accepts(corpus: None) -> None:
    m = ScriptedSupervisorModel(
        responder=responder(
            [
                [
                    call(
                        "sql_query",
                        {"sql": "SELECT count(*) AS n FROM v_postings WHERE is_active LIMIT 1"},
                    )
                ]
            ],
            ["There are 9 active postings."],
        ),
        structured=[{"route": "data", "reason": "count"}, {"ok": True, "issues": ""}],
    )
    out = await SupervisorAgent(model=m).ainvoke("How many postings?")
    assert (
        out["route"] == "data"
        and out["answer"] == "There are 9 active postings."
        and not out["revised"]
    )
    assert [c["tool"] for c in out["tool_calls"]] == ["sql_query"]


async def test_critic_requests_one_revision(corpus: None) -> None:
    m = ScriptedSupervisorModel(
        responder=responder(
            [[call("search_postings", {"query": "RAG"})]],
            [
                "Great job [an-0000000001], salary 1M.",
                "The RAG role [an-0000000001] pays 72,000–85,000 EUR (indicative).",
            ],
        ),
        structured=[
            {"route": "search", "reason": ""},
            {"ok": False, "issues": "salary 1M not in results"},
        ],
    )
    out = await SupervisorAgent(model=m).ainvoke("RAG jobs?")
    assert out["revised"] and "72,000" in out["answer"] and out["citations"] == ["an-0000000001"]


async def test_out_of_scope_uses_no_tools_and_streams(corpus: None) -> None:
    m = ScriptedSupervisorModel(
        responder=responder([], ["Sorry, I only help with jobs in Germany."]),
        structured=[{"route": "out_of_scope", "reason": ""}],
    )
    agent = SupervisorAgent(model=m)
    events = [e async for e in agent.astream_events("Write a poem", "s1")]
    assert events[-1]["type"] == "done" and events[-1]["route"] == "out_of_scope"
    assert events[-1]["tool_calls"] == []


async def test_invalid_route_falls_back_to_search(corpus: None) -> None:
    m = ScriptedSupervisorModel(
        responder=responder([], ["ok"]), structured=[{"route": "weird"}, {"ok": True, "issues": ""}]
    )
    out = await SupervisorAgent(model=m).ainvoke("hm")
    assert out["route"] == "search"
