"""LangGraph StateGraph agent.

    START → router ─(tool calls, budget left)→ tools → router …
              │                       └─(ask_clarification)→ END
              └─(no tool calls | budget exhausted)→ composer → citation_check
    citation_check ─(invalid IDs, first time)→ composer (regenerate once)
                   └─(ok | second time: strip + warning)→ END

- tools run concurrently (asyncio.gather) and identical duplicate calls are dropped;
- max ``agent_max_tool_steps`` tool calls per question, then a fallback answer;
- per-session state via a checkpointer (thread_id = session id);
- custom stream events: tool_call, tool_result, citation, reset.
"""

from __future__ import annotations

import asyncio
import json
import operator
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from jobscout.agent import prompts
from jobscout.agent.citations import check_citations, ids_in, strip_invalid
from jobscout.agent.recovery import recover_tool_calls
from jobscout.llm import get_chat_model, usage_of
from jobscout.metrics import (
    CITATION_CHECKS,
    LLM_CALLS,
    LLM_COST,
    LLM_TOKENS,
    TOOL_CALLS,
    TOOL_CALLS_RECOVERED,
)
from jobscout.observability import span
from jobscout.settings import get_settings
from jobscout.tools.impl import TOOLS, run_tool, wrap_untrusted


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    language: str
    tool_steps: int
    turn_results: list[dict[str, Any]]
    retrieved_ids: Annotated[list[str], operator.add]
    answer: str
    draft: str
    citation_attempts: int
    invalid_citations: list[str]
    citations: list[str]
    warnings: list[str]
    clarification: str | None
    budget_exhausted: bool
    tokens_in: int
    tokens_out: int
    citation_history: list[dict[str, int]]


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $defs/$ref so small models and all providers get a flat JSON schema."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    out: dict[str, Any] = resolve(schema)
    return out


def tool_definitions(tool_names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description.split("\n")[0],
                "parameters": _inline_refs(spec.input_model.model_json_schema()),
            },
        }
        for spec in TOOLS.values()
        if tool_names is None or spec.name in tool_names
    ]


def _recent_history(messages: Sequence[BaseMessage], turns: int = 3) -> list[BaseMessage]:
    """Keep the last ``turns`` user turns (never start with a dangling ToolMessage)."""
    idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    start = idx[-turns] if len(idx) >= turns else 0
    return list(messages[start:])


def _record_cost(role: str, tokens_in: int, tokens_out: int) -> None:
    from jobscout.evaluation.runner import cost_eur

    s = get_settings()
    c = cost_eur(f"{s.llm_provider}:{s.chat_model}", tokens_in, tokens_out)
    if c:
        LLM_COST.labels(role=role).inc(c)


def _writer() -> Any:
    try:
        return get_stream_writer()
    except Exception:  # outside a streaming run
        return lambda _event: None


def build_graph(
    model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    max_steps: int | None = None,
    tool_names: Sequence[str] | None = None,
    router_note: str = "",
) -> CompiledStateGraph[Any, Any, Any, Any]:
    s = get_settings()
    chat = model or get_chat_model("chat")
    max_steps = max_steps or s.agent_max_tool_steps
    router_llm = chat.bind_tools(tool_definitions(tool_names))

    async def router(state: AgentState) -> dict[str, Any]:
        history = _recent_history(state.get("messages", []))
        msgs: list[BaseMessage] = [SystemMessage(prompts.router_system() + router_note), *history]
        with span("agent.router", step=state.get("tool_steps", 0)):
            ai = await router_llm.ainvoke(msgs)
        ti, to = usage_of(ai)
        if not ai.tool_calls and isinstance(ai.content, str):
            recovered = recover_tool_calls(ai.content, tool_names or TOOLS)
            if recovered:
                TOOL_CALLS_RECOVERED.inc()
                ai = AIMessage(content="", tool_calls=recovered, id=ai.id)
        LLM_CALLS.labels(role="router", outcome="ok").inc()
        LLM_TOKENS.labels(role="router", direction="in").inc(ti)
        LLM_TOKENS.labels(role="router", direction="out").inc(to)
        _record_cost("router", ti, to)
        return {
            "messages": [ai],
            "tokens_in": state.get("tokens_in", 0) + ti,
            "tokens_out": state.get("tokens_out", 0) + to,
        }

    def after_router(state: AgentState) -> Literal["tools", "composer"]:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls:
            return "composer"
        if state.get("tool_steps", 0) >= max_steps:
            return "composer"
        return "tools"

    async def tools(state: AgentState) -> dict[str, Any]:
        write = _writer()
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        budget = max_steps - state.get("tool_steps", 0)
        seen: set[str] = set()
        calls = []
        for call in last.tool_calls:
            key = call["name"] + json.dumps(call.get("args", {}), sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            calls.append(call)
        executed, skipped = calls[:budget], calls[budget:]

        async def one(call: Any) -> tuple[Any, dict[str, Any], float]:
            write(
                {
                    "type": "tool_call",
                    "id": call["id"],
                    "name": call["name"],
                    "args": call.get("args", {}),
                }
            )
            try:
                with span("tool." + (call["name"] if call["name"] in TOOLS else "unknown")):
                    result, secs = await run_tool(call["name"], call.get("args") or {})
            except Exception as exc:  # tool crashed: report, don't kill the graph
                result, secs = {"error": f"tool failed: {type(exc).__name__}"}, 0.0
            outcome = "error" if "error" in result and len(result) == 1 else "ok"
            TOOL_CALLS.labels(
                tool=call["name"] if call["name"] in TOOLS else "unknown", outcome=outcome
            ).inc()
            write(
                {
                    "type": "tool_result",
                    "id": call["id"],
                    "name": call["name"],
                    "result": result,
                    "seconds": round(secs, 3),
                }
            )
            return call, result, secs

        with span("agent.tools", n_calls=len(executed)):
            results = await asyncio.gather(*(one(c) for c in executed))
        tool_msgs: list[AnyMessage] = []
        turn = list(state.get("turn_results", []))
        new_ids: set[str] = set()
        clarification = None
        for call, result, _ in results:
            tool_msgs.append(
                ToolMessage(
                    content=wrap_untrusted(call["name"], result),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
            turn.append({"tool": call["name"], "args": call.get("args", {}), "result": result})
            new_ids |= ids_in(result)
            if call["name"] == "ask_clarification" and "question" in result:
                clarification = result["question"]
        for call in skipped:
            tool_msgs.append(
                ToolMessage(
                    content="skipped: tool budget exhausted",
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
        # Every AI tool call must be answered by a ToolMessage (provider requirement).
        for call in last.tool_calls:
            if call not in executed and call not in skipped:
                tool_msgs.append(
                    ToolMessage(
                        content="duplicate call skipped", tool_call_id=call["id"], name=call["name"]
                    )
                )
        steps = state.get("tool_steps", 0) + len(executed)
        update: dict[str, Any] = {
            "messages": tool_msgs,
            "tool_steps": steps,
            "turn_results": turn,
            "retrieved_ids": sorted(new_ids),
            "budget_exhausted": steps >= max_steps,
        }
        if clarification:
            update["clarification"] = clarification
            update["answer"] = clarification
        return update

    def after_tools(state: AgentState) -> Literal["router", "composer", "__end__"]:
        if state.get("clarification"):
            return "__end__"
        if state.get("tool_steps", 0) >= max_steps:
            return "composer"
        return "router"

    async def composer(state: AgentState) -> dict[str, Any]:
        lang = prompts.LANG_NAMES.get(state.get("language", "en"), "the user's language")
        extra = ""
        if state.get("invalid_citations"):
            extra += prompts.REGENERATE_NOTE.format(invalid=", ".join(state["invalid_citations"]))
        if state.get("budget_exhausted"):
            extra += prompts.BUDGET_NOTE
        results = state.get("turn_results", [])
        context = (
            "\n\n".join(wrap_untrusted(r["tool"], r["result"]) for r in results)
            or "(no tool results)"
        )
        msgs = [
            SystemMessage(prompts.COMPOSER_SYSTEM.format(language=lang, extra=extra)),
            HumanMessage(f"Question: {state['question']}\n\nTool results:\n{context}"),
        ]
        with span("agent.composer", regenerate=bool(state.get("invalid_citations"))):
            ai = await chat.ainvoke(msgs)
        ti, to = usage_of(ai)
        _record_cost("composer", ti, to)
        LLM_CALLS.labels(role="composer", outcome="ok").inc()
        LLM_TOKENS.labels(role="composer", direction="in").inc(ti)
        LLM_TOKENS.labels(role="composer", direction="out").inc(to)
        return {
            "draft": str(ai.content),
            "tokens_in": state.get("tokens_in", 0) + ti,
            "tokens_out": state.get("tokens_out", 0) + to,
        }

    def citation_check(state: AgentState) -> dict[str, Any]:
        write = _writer()
        draft = state.get("draft", "")
        check = check_citations(draft, state.get("retrieved_ids", []))
        attempts = state.get("citation_attempts", 0) + 1
        warnings = list(state.get("warnings", []))
        history = [
            *state.get("citation_history", []),
            {"cited": len(check.cited), "invalid": len(check.invalid)},
        ]
        if check.ok:
            CITATION_CHECKS.labels(outcome="ok" if attempts == 1 else "ok_after_regen").inc()
            write({"type": "citation", "ids": check.valid})
            return {
                "answer": draft,
                "citations": check.valid,
                "citation_attempts": attempts,
                "invalid_citations": [],
                "warnings": warnings,
                "citation_history": history,
            }
        if attempts < 2:
            CITATION_CHECKS.labels(outcome="regenerate").inc()
            write({"type": "reset", "reason": "invalid citations", "invalid": check.invalid})
            return {
                "citation_attempts": attempts,
                "invalid_citations": check.invalid,
                "citation_history": history,
            }
        CITATION_CHECKS.labels(outcome="failed").inc()
        warnings.append(
            "Some references could not be verified against the retrieved postings and were "
            "marked 'unverified'. Please double-check those claims."
        )
        write({"type": "citation", "ids": check.valid})
        return {
            "answer": strip_invalid(draft, check.invalid),
            "citations": check.valid,
            "citation_attempts": attempts,
            "warnings": warnings,
            "citation_history": history,
        }

    def after_check(state: AgentState) -> Literal["composer", "__end__"]:
        return (
            "composer" if state.get("invalid_citations") and not state.get("answer") else "__end__"
        )

    g = StateGraph(AgentState)
    g.add_node("router", router)
    g.add_node("tools", tools)
    g.add_node("composer", composer)
    g.add_node("citation_check", citation_check)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", after_router, {"tools": "tools", "composer": "composer"})
    g.add_conditional_edges(
        "tools", after_tools, {"router": "router", "composer": "composer", "__end__": END}
    )
    g.add_edge("composer", "citation_check")
    g.add_conditional_edges("citation_check", after_check, {"composer": "composer", "__end__": END})
    return g.compile(checkpointer=checkpointer)


def turn_input(question: str) -> dict[str, Any]:
    """Per-turn fields are reset; messages and retrieved_ids accumulate per session."""
    return {
        "messages": [HumanMessage(question)],
        "question": question,
        "language": prompts.detect_user_language(question),
        "tool_steps": 0,
        "turn_results": [],
        "answer": "",
        "draft": "",
        "citation_attempts": 0,
        "invalid_citations": [],
        "citations": [],
        "warnings": [],
        "clarification": None,
        "budget_exhausted": False,
        "tokens_in": 0,
        "tokens_out": 0,
        "citation_history": [],
    }


class Agent:
    """Thin wrapper: one compiled graph + an in-process checkpointer for sessions."""

    def __init__(self, model: BaseChatModel | None = None, max_steps: int | None = None) -> None:
        self.checkpointer = InMemorySaver()
        self.graph = build_graph(model, self.checkpointer, max_steps)

    def _config(self, session_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 40}

    async def ainvoke(self, question: str, session_id: str | None = None) -> dict[str, Any]:
        session_id = session_id or uuid.uuid4().hex
        t0 = time.perf_counter()
        state = await self.graph.ainvoke(turn_input(question), self._config(session_id))
        return {
            **_public(state),
            "session_id": session_id,
            "seconds": round(time.perf_counter() - t0, 3),
        }

    async def astream_events(self, question: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yields {'type': token|tool_call|tool_result|citation|reset|done, ...}."""
        t0 = time.perf_counter()
        final: dict[str, Any] = {}
        stream: AsyncIterator[Any] = self.graph.astream(  # multi-mode yields (mode, chunk) tuples
            turn_input(question),
            self._config(session_id),
            stream_mode=["messages", "custom", "values"],
        )
        async for mode, chunk in stream:
            if mode == "custom":
                yield chunk
            elif mode == "messages":
                msg, meta = chunk
                if meta.get("langgraph_node") == "composer" and getattr(msg, "content", None):
                    yield {"type": "token", "text": str(msg.content)}
            elif mode == "values":
                final = chunk
        yield {
            "type": "done",
            **_public(final),
            "session_id": session_id,
            "seconds": round(time.perf_counter() - t0, 3),
        }


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": state.get("answer", ""),
        "citations": state.get("citations", []),
        "warnings": state.get("warnings", []),
        "clarification": state.get("clarification"),
        "tool_calls": [
            {"tool": r["tool"], "args": r["args"]} for r in state.get("turn_results", [])
        ],
        "tool_results": state.get("turn_results", []),
        "tool_steps": state.get("tool_steps", 0),
        "language": state.get("language", "en"),
        "tokens_in": state.get("tokens_in", 0),
        "tokens_out": state.get("tokens_out", 0),
        "citation_attempts": state.get("citation_attempts", 0),
        "citation_history": state.get("citation_history", []),
    }
