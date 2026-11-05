"""Pure-ASGI middleware (safe for streaming responses): request IDs, structured access
logs and request-latency metrics with bounded labels (route template, not raw path)."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jobscout.logging import get_logger
from jobscout.metrics import REQUEST_LATENCY

log = get_logger("jobscout.access")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route: Any = scope.get("route")
            template = getattr(route, "path", None) or (
                "static" if scope["path"].startswith("/assets") else "unmatched"
            )
            REQUEST_LATENCY.labels(
                route=template, method=scope["method"], status_class=f"{status // 100}xx"
            ).observe(elapsed)
            if template not in ("/healthz", "/metrics"):
                log.info(
                    "request",
                    method=scope["method"],
                    route=template,
                    status=status,
                    duration_ms=round(elapsed * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()
