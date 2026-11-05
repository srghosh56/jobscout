"""LLM provider layer: one switch (``LLM_PROVIDER``) for chat, judge, extraction and
embedding models. Everything is exposed as LangChain chat/embedding models so the
LangGraph agent is provider-agnostic.

Providers: ``ollama`` (local, default), ``mistral`` (hosted API), ``gemini`` (hosted
API, chat only), ``fake`` (deterministic, for tests/CI: no network).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda

from jobscout.settings import Provider, get_settings

Role = Literal["chat", "judge", "extract"]


def model_name(role: Role, provider: Provider | None = None) -> str:
    s = get_settings()
    provider = provider or s.llm_provider
    if provider == "gemini" and role == "chat":
        return s.gemini_chat_model
    if role == "judge":
        return s.judge_model
    if role == "extract":
        return s.effective_extract_model
    return s.chat_model


def model_version(role: Role, provider: Provider | None = None) -> str:
    provider = provider or get_settings().llm_provider
    return f"{provider}:{model_name(role, provider)}"


def get_chat_model(
    role: Role = "chat",
    provider: Provider | None = None,
    temperature: float | None = None,
    fake_responder: Callable[[Sequence[BaseMessage]], AIMessage] | None = None,
) -> BaseChatModel:
    s = get_settings()
    provider = provider or s.llm_provider
    temp = s.llm_temperature if temperature is None else temperature
    name = model_name(role, provider)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=name,
            base_url=s.ollama_base_url,
            temperature=temp,
            num_ctx=8192,
            client_kwargs={"timeout": s.llm_timeout_s},
        )
    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        if not s.mistral_api_key or not s.mistral_api_key.get_secret_value():
            raise RuntimeError("LLM_PROVIDER=mistral needs MISTRAL_API_KEY in .env")
        return ChatMistralAI(
            model_name=name,
            api_key=s.mistral_api_key,
            temperature=temp,
            timeout=int(s.llm_timeout_s),
        )
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not s.gemini_api_key or not s.gemini_api_key.get_secret_value():
            raise RuntimeError("LLM_PROVIDER=gemini needs GEMINI_API_KEY in .env")
        return ChatGoogleGenerativeAI(
            model=name, api_key=s.gemini_api_key, temperature=temp, timeout=s.llm_timeout_s
        )
    if provider == "fake":
        return FakeChatModel(responder=fake_responder)
    raise ValueError(f"unknown provider {provider}")


# ---------------------------------------------------------------------------
# Fake chat model (tests / CI)
# ---------------------------------------------------------------------------
class FakeChatModel(BaseChatModel):
    """Deterministic chat model.

    - ``responder`` (callable or list of AIMessages) scripts the replies, including
      tool calls, so agent graph logic can be tested without an LLM.
    - ``with_structured_output`` returns rule-based extraction results for JobLens
      postings and CV profiles, so the pipeline runs end to end offline.
    """

    responder: Any = None
    script: list[AIMessage] = []  # noqa: RUF012 - pydantic field (copied per instance)
    calls: list[list[BaseMessage]] = []  # noqa: RUF012 - pydantic field

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if callable(self.responder):
            out = self.responder(messages)
            assert isinstance(out, AIMessage)
            return out
        if self.script:
            return self.script.pop(0)
        return AIMessage(content="(fake model) no scripted reply")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = self._next(messages)
        msg.usage_metadata = msg.usage_metadata or {
            "input_tokens": sum(len(str(m.content)) // 4 for m in messages),
            "output_tokens": len(str(msg.content)) // 4,
            "total_tokens": 0,
        }
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[Any, Any]:
        title = schema.get("title") if isinstance(schema, dict) else getattr(schema, "__name__", "")

        def run(inp: Any) -> Any:
            msgs = inp if isinstance(inp, list) else [inp]
            text = str(msgs[-1].content if hasattr(msgs[-1], "content") else msgs[-1][1])
            from jobscout.extract import rules

            parsed: dict[str, Any]
            if title == "CandidateProfile":
                parsed = rules.rule_based_profile(text)
            else:
                parsed = rules.rule_based_extract(text)
            raw = AIMessage(
                content=json.dumps(parsed),
                usage_metadata={
                    "input_tokens": len(text) // 4,
                    "output_tokens": len(json.dumps(parsed)) // 4,
                    "total_tokens": 0,
                },
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(run)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
class HashingEmbeddings(Embeddings):
    """Deterministic bag-of-words hashing embeddings (fake provider). Similar texts get
    similar vectors, so retrieval tests are meaningful without a model."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def embed_model_version(provider: Provider | None = None) -> str:
    s = get_settings()
    provider = provider or s.effective_embed_provider
    if provider == "fake":
        return "fake:hashing-1024"
    if provider == "mistral":
        return "mistral:mistral-embed"
    if provider == "st":
        # Same weights as Ollama's bge-m3; recorded under the Ollama name so stored vectors
        # stay valid (verified by tests/test_llm.py::test_st_matches_ollama when both exist).
        return (
            f"ollama:{s.embed_model}"
            if s.st_embed_model == "BAAI/bge-m3"
            else f"st:{s.st_embed_model}"
        )
    return f"{provider}:{s.embed_model}"


def get_embeddings(provider: Provider | None = None) -> Embeddings:
    s = get_settings()
    provider = provider or s.effective_embed_provider
    if provider == "fake":
        return HashingEmbeddings(s.embed_dim)
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=s.embed_model, base_url=s.ollama_base_url)
    if provider == "mistral":
        from langchain_mistralai import MistralAIEmbeddings

        if not s.mistral_api_key or not s.mistral_api_key.get_secret_value():
            raise RuntimeError("EMBED_PROVIDER=mistral needs MISTRAL_API_KEY")
        return MistralAIEmbeddings(model="mistral-embed", api_key=s.mistral_api_key)  # type: ignore[call-arg]
    if provider == "st":
        return SentenceTransformerEmbeddings(s.st_embed_model)
    raise ValueError(f"embedding provider {provider} not supported (use ollama|mistral|st|fake)")


class SentenceTransformerEmbeddings(Embeddings):
    """Local CPU embeddings without Ollama (e.g. on a Hugging Face Space)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True, batch_size=16)
        return [list(map(float, v)) for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def usage_of(msg: BaseMessage) -> tuple[int, int]:
    um = getattr(msg, "usage_metadata", None) or {}
    return int(um.get("input_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0)
