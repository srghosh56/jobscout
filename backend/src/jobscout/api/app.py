"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobscout import __version__
from jobscout.api.middleware import RequestContextMiddleware
from jobscout.api.ratelimit import RateLimiter
from jobscout.api.routes import ops, router
from jobscout.db.engine import dispose_engines
from jobscout.logging import configure_logging, get_logger
from jobscout.observability import setup_tracing
from jobscout.settings import REPO_ROOT, get_settings

log = get_logger(__name__)

DESCRIPTION = """JobScout API: search skilled jobs in Germany for international candidates.

Authentication: send `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Get a
rate-limited demo key from `POST /api/demo-key`. Blue Card output is indicative, not legal
advice. Posting texts are never returned in full; follow `url` to the original ad."""


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, json=s.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from jobscout.agent.graph import Agent

        if s.agent_mode == "supervisor":
            from jobscout.agent.supervisor import SupervisorAgent

            app.state.agent = SupervisorAgent()
        else:
            app.state.agent = Agent()
        log.info("startup", version=__version__, provider=s.llm_provider, agent_mode=s.agent_mode)
        yield
        await dispose_engines()

    app = FastAPI(
        title="JobScout API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        license_info={"name": "MIT"},
    )
    app.state.limiter = RateLimiter()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["X-API-Key", "Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Session-Id", "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(ops)
    app.include_router(router)
    try:
        from jobscout.api.routes_public import public

        app.include_router(public)
    except ImportError:  # pragma: no cover - added in a later step
        pass

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=type(exc).__name__, path=request.url.path)
        return JSONResponse(
            {"detail": "internal error", "request_id": getattr(request.state, "request_id", None)},
            status_code=500,
        )

    static = s.static_dir or (REPO_ROOT / "frontend" / "dist")
    if Path(static).is_dir() and (Path(static) / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=Path(static) / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:  # sync: FastAPI runs it in a thread (file I/O)
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, "not found")
            candidate = (Path(static) / full_path).resolve()
            if full_path and candidate.is_file() and Path(static).resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(Path(static) / "index.html")

    setup_tracing(app)
    return app
