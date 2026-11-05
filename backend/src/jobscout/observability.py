"""OpenTelemetry tracing: request → agent step → LLM call → tool → DB, exported via
OTLP/HTTP (Jaeger in Compose). Disabled unless OTEL_ENABLED=true."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from jobscout import __version__
from jobscout.settings import get_settings

tracer = trace.get_tracer("jobscout")
_configured = False


def setup_tracing(app: Any | None = None) -> bool:
    global _configured
    s = get_settings()
    if not s.otel_enabled or _configured:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    provider = TracerProvider(
        resource=Resource.create({"service.name": s.service_name, "service.version": __version__})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)
    from jobscout.db.engine import get_engine, get_readonly_engine

    SQLAlchemyInstrumentor().instrument(
        engines=[get_engine().sync_engine, get_readonly_engine().sync_engine]
    )
    HTTPXClientInstrumentor().instrument()
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")
    _configured = True
    return True


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Span with low-cardinality attributes only (never user text or CV content)."""
    with tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp
