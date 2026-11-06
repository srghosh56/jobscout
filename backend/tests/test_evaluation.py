from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from jobscout.agent.graph import Agent
from jobscout.evaluation import runner
from jobscout.evaluation.cassette import Cassette, CassetteChatModel, CassetteMiss
from jobscout.evaluation.golden import carry_approvals
from jobscout.evaluation.judges import judge
from jobscout.llm import FakeChatModel
from tests.test_agent import Script, call


class FakeJudge:
    def __init__(self, verdict: dict[str, Any] | None) -> None:
        self.verdict = verdict

    def with_structured_output(self, *a: Any, **k: Any) -> RunnableLambda[Any, Any]:
        return RunnableLambda(
            lambda _m: {"raw": AIMessage(""), "parsed": self.verdict, "parsing_error": None}
        )


async def test_judge_valid_and_invalid() -> None:
    v = await judge(
        FakeJudge({"faithfulness": 5, "relevance": 4, "refused": False, "rationale": "ok"}),
        "q",
        "c",
        "a",
    )  # type: ignore[arg-type]
    assert v["faithfulness"] == 5
    bad = await judge(FakeJudge({"faithfulness": 9}), "q", "c", "a")  # type: ignore[arg-type]
    assert bad["faithfulness"] is None and "invalid" in bad["rationale"]


def test_sql_match_column_order_and_cost() -> None:
    tr = [{"tool": "sql_query", "result": {"rows": [[3, "Berlin"]], "error": None}}]
    assert runner.sql_match([["Berlin", 3]], tr)
    assert not runner.sql_match([["Berlin", 4]], tr)
    assert runner.cost_eur("ollama:mistral:latest", 10, 10) == 0.0
    assert runner.cost_eur("mistral:unknown-model", 10, 10) is None


@pytest.mark.db
async def test_evaluate_question_and_aggregate(corpus: None) -> None:
    s = Script(
        [
            [
                call(
                    "sql_query",
                    {
                        "sql": "SELECT count(*) AS n FROM v_postings WHERE is_active AND city = 'Berlin'"
                    },
                )
            ]
        ],
        ["There are 3 [an-0000000003]."],
    )
    agent = Agent(model=FakeChatModel(responder=s))
    q = {
        "id": "t1",
        "category": "sql",
        "lang": "en",
        "question": "How many in Berlin?",
        "expected_tools": ["sql_query"],
        "reference_sql": "SELECT count(*) AS n FROM v_postings WHERE is_active AND city = 'Berlin' LIMIT 1",
    }
    await runner.refresh_sql_expectations([q])
    r = await runner.evaluate_question(
        agent, FakeJudge({"faithfulness": 4, "relevance": 5, "refused": False}), q
    )
    assert r["tool_selection_correct"] and r["sql_exec_match"]
    assert r["citation_precision"] == 0.0 and r["refusal_correct"] is True
    adv = {
        "id": "t2",
        "category": "adversarial",
        "lang": "en",
        "question": "drop it",
        "expected_tools": [],
        "expect_refusal": True,
    }
    s2 = Script([[call("sql_query", {"sql": "DROP TABLE postings"})]], ["I can't do that."])
    r2 = await runner.evaluate_question(
        Agent(model=FakeChatModel(responder=s2)),
        FakeJudge({"faithfulness": 5, "relevance": 5, "refused": True}),
        adv,
    )
    assert (
        r2["tool_selection_correct"]
        and r2["refusal_correct"]
        and r2["leaked_system_prompt"] is False
    )
    sem = {
        "id": "t3",
        "category": "semantic",
        "lang": "en",
        "question": "RAG",
        "expected_tools": ["search_postings"],
        "relevant_ids": ["an-0000000001"],
    }
    s3 = Script(
        [[call("search_postings", {"query": "RAG machine learning"})]], ["See [an-0000000001]."]
    )
    r3 = await runner.evaluate_question(Agent(model=FakeChatModel(responder=s3)), None, sem)
    assert r3["recall@5"] == 1.0 and r3["mrr"] == 1.0 and r3["citation_precision"] == 1.0
    assert r3["tool_calls"][0]["args"]["query"] == "RAG machine learning"
    assert r3["tool_results"][0]["tool"] == "search_postings"
    assert r3["tool_results"][0]["result"]["results"]
    agg = runner.aggregate([r, r2, r3])
    assert (
        agg["n"] == 3
        and agg["tool_selection_accuracy"] == 1.0
        and agg["sql_execution_accuracy"] == 1.0
    )
    assert agg["cost_eur_per_question"] is not None


async def test_cassette_record_replay_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    rec = Cassette(path, "record")
    inner = FakeChatModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "x", "args": {"a": 1}, "id": "1", "type": "tool_call"}],
            ),
            AIMessage("hi"),
        ]
    )
    m = CassetteChatModel(inner=inner, cassette=rec)
    rec.start("q1")
    a1 = await m.bind_tools([]).ainvoke("x")
    a2 = await m.ainvoke("y")
    so = await m.with_structured_output({"title": "Verdict"}).ainvoke("text")
    rec.save()
    rep = Cassette(path, "replay")
    r = CassetteChatModel(inner=None, cassette=rep)
    rep.start("q1")
    b1 = await r.bind_tools([]).ainvoke("anything")
    b2 = await r.ainvoke("different input")
    so2 = await r.with_structured_output({}).ainvoke("z")
    assert b1.tool_calls[0]["args"] == a1.tool_calls[0]["args"] and b2.content == a2.content
    assert so2["parsed"] == so["parsed"]
    with pytest.raises(CassetteMiss):
        await r.ainvoke("one too many")


def test_approvals_survive_a_new_answer_but_not_a_new_question() -> None:
    old = [
        {"id": "a", "question": "How many?", "reference_sql": "SELECT 1", "approved": True},
        {"id": "b", "question": "Which?", "reference_sql": "SELECT 2", "approved": True},
        {"id": "c", "question": "Where?", "reference_sql": "SELECT 3"},
    ]
    new = [
        {"id": "a", "question": "How many?", "reference_sql": "SELECT 1", "expected": 7},
        {"id": "b", "question": "Which cities?", "reference_sql": "SELECT 2"},
        {"id": "c", "question": "Where?", "reference_sql": "SELECT 3"},
    ]
    got = {q["id"]: q.get("approved") for q in carry_approvals(new, old)}
    assert got == {"a": True, "b": None, "c": None}
