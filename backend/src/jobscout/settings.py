"""Application settings, read from environment variables and the repo-root .env file.

Secrets (API keys, DB passwords) are held as ``SecretStr`` so they are never printed
by accident in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"

Provider = Literal["ollama", "mistral", "gemini", "fake", "st"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM / embeddings -------------------------------------------------
    llm_provider: Provider = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "mistral:latest"
    judge_model: str = "mistral:latest"
    extract_model: str | None = None  # defaults to chat_model
    embed_provider: Provider | None = None  # defaults to llm_provider
    embed_model: str = "bge-m3:latest"
    st_embed_model: str = "BAAI/bge-m3"  # EMBED_PROVIDER=st: same model via sentence-transformers
    embed_dim: int = 1024
    mistral_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_chat_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.0
    llm_timeout_s: float = 180.0

    # --- Database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://jobscout:jobscout@localhost:55433/jobscout"
    readonly_database_url: str = (
        "postgresql+asyncpg://jobscout_ro:jobscout_ro@localhost:55433/jobscout"
    )
    sql_statement_timeout_ms: int = 3000
    sql_max_rows: int = 50

    # --- Retrieval ---------------------------------------------------------
    rrf_k: int = 60
    retrieval_candidates: int = 50
    rerank_top_k: int = 8
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_enabled: bool = True

    # --- Agent -------------------------------------------------------------
    agent_max_tool_steps: int = 6
    agent_mode: Literal["single", "supervisor"] = "single"

    # --- API ---------------------------------------------------------------
    api_auth_enabled: bool = True
    demo_key_enabled: bool = True
    rate_limit_capacity: int = 30
    rate_limit_refill_per_s: float = 0.5
    demo_rate_limit_capacity: int = 5
    demo_rate_limit_refill_per_s: float = 0.05
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    static_dir: Path | None = None
    public_base_url: str = "http://localhost:8088"
    # Restrict which sources are shown (e.g. ["arbeitsagentur"] for a public deploy; see DATA_SOURCES.md)
    public_sources: list[str] | None = None

    # --- Ingestion ---------------------------------------------------------
    http_user_agent: str = (
        "JobScout/0.1 (non-commercial portfolio project; polite crawler, 1 req/s)"
    )
    http_min_interval_s: float = 1.0
    http_cache_dir: Path = REPO_ROOT / ".cache" / "http"

    # --- Observability -----------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    service_name: str = "jobscout-api"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Privacy -----------------------------------------------------------
    cv_store_opt_in_allowed: bool = True

    @property
    def effective_extract_model(self) -> str:
        return self.extract_model or self.chat_model

    @property
    def effective_embed_provider(self) -> Provider:
        return self.embed_provider or self.llm_provider


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
