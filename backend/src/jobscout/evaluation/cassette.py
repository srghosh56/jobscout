"""Record/replay of LLM calls for the CI smoke evaluation (no API keys in CI).

Calls are keyed by (question id, call index) and replayed in order, so a replay is
deterministic as long as the graph makes the same sequence of LLM calls. Cassettes
are recorded against the synthetic corpus only, so they never contain real postings.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda

Mode = Literal["record", "replay"]


class CassetteMiss(KeyError):
    pass


class Cassette:
    def __init__(self, path: Path, mode: Mode) -> None:
        self.path, self.mode = path, mode
        self.data: dict[str, Any] = json.loads(path.read_text()) if mode == "replay" else {}
        self.scope = "default"
        self.counter = 0

    def start(self, scope: str) -> None:
        self.scope, self.counter = scope, 0

    def _key(self) -> str:
        self.counter += 1
        return f"{self.scope}:{self.counter}"

    async def call(self, fn: Any) -> Any:
        key = self._key()
        if self.mode == "replay":
            if key not in self.data:
                raise CassetteMiss(key)
            return self.data[key]
        value = await fn()
        self.data[key] = value
        return value

    def save(self) -> None:
        if self.mode == "record":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True)
            )


def _dump(msg: AIMessage) -> dict[str, Any]:
    return {
        "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
        "tool_calls": [
            {"name": c["name"], "args": c["args"], "id": c["id"]} for c in msg.tool_calls
        ],
        "usage": dict(msg.usage_metadata or {}),
    }


def _load(d: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content=d["content"],
        tool_calls=[{**c, "type": "tool_call"} for c in d["tool_calls"]],
        usage_metadata=d.get("usage") or None,
    )


class CassetteChatModel(BaseChatModel):
    inner: Any = None
    cassette: Any = None
    bound_tools: Any = None

    @property
    def _llm_type(self) -> str:
        return "cassette"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> CassetteChatModel:
        bound = self.inner.bind_tools(tools, **kwargs) if self.inner is not None else None
        return CassetteChatModel(inner=self.inner, cassette=self.cassette, bound_tools=bound)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kw: Any,
    ) -> ChatResult:
        raise NotImplementedError("async only")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kw: Any,
    ) -> ChatResult:
        target = self.bound_tools or self.inner

        async def real() -> dict[str, Any]:
            return _dump(await target.ainvoke(messages))

        data = await self.cassette.call(real)
        return ChatResult(generations=[ChatGeneration(message=_load(data))])

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        inner_runnable = (
            self.inner.with_structured_output(schema, **kwargs) if self.inner is not None else None
        )

        async def run(inp: Any) -> Any:
            async def real() -> dict[str, Any]:
                if inner_runnable is None:
                    raise CassetteMiss("no inner model in replay mode")
                out = await inner_runnable.ainvoke(inp)
                parsed: Any = out.get("parsed") if isinstance(out, dict) else out
                return {"parsed": parsed.model_dump() if hasattr(parsed, "model_dump") else parsed}

            data = await self.cassette.call(real)
            return {
                "raw": AIMessage(json.dumps(data["parsed"])),
                "parsed": data["parsed"],
                "parsing_error": None,
            }

        return RunnableLambda(run)
