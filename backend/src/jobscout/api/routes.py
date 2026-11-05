"""HTTP routes (all JSON routes under /api; health and metrics at the root)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sse_starlette import EventSourceResponse, ServerSentEvent

from jobscout import __version__
from jobscout.api.auth import Principal, create_api_key, key_from_request, verify_key
from jobscout.db.engine import session_scope
from jobscout.db.models import Feedback, Posting, SavedSearch
from jobscout.logging import get_logger
from jobscout.metrics import FEEDBACK, RATE_LIMITED, TIME_TO_FIRST_TOKEN
from jobscout.retrieval.filters import SearchFilters
from jobscout.retrieval.search import browse, hybrid_search
from jobscout.settings import get_settings
from jobscout.tools import schemas as S
from jobscout.tools.impl import detail_from_row, summary_from_hit

log = get_logger(__name__)
router = APIRouter(prefix="/api")
ops = APIRouter()


# --- auth + rate limit dependency ---------------------------------------------------
def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(
    request: Request, bucket: str, capacity: float, refill: float, cost: float, tier: str
) -> None:
    ok, retry = request.app.state.limiter.check(bucket, capacity, refill, cost)
    if not ok:
        RATE_LIMITED.labels(tier=tier).inc()
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(retry)})


def require_key(cost: float = 1.0) -> Any:
    async def dep(request: Request) -> Principal:
        s = get_settings()
        if not s.api_auth_enabled:
            return Principal(key_id=0, name="anonymous", is_demo=False)
        principal = await verify_key(key_from_request(request))
        if principal is None:
            raise HTTPException(
                401, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"}
            )
        if principal.is_demo:
            _limit(
                request,
                f"k{principal.key_id}",
                s.demo_rate_limit_capacity,
                s.demo_rate_limit_refill_per_s,
                cost,
                "demo",
            )
        else:
            _limit(
                request,
                f"k{principal.key_id}",
                s.rate_limit_capacity,
                s.rate_limit_refill_per_s,
                cost,
                "standard",
            )
        request.state.principal = principal
        return principal

    return dep


Auth = Annotated[Principal, Depends(require_key())]
ChatAuth = Annotated[Principal, Depends(require_key(cost=3.0))]


def _key_id(p: Principal) -> int | None:
    return p.key_id or None


# --- ops ------------------------------------------------------------------------------
@ops.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@ops.get("/readyz", include_in_schema=False)
async def readyz(response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
            checks["database"] = "ok"
            checks["migration"] = (
                await s.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            checks["active_postings"] = (
                await s.execute(
                    text("SELECT count(*) FROM postings WHERE is_active AND duplicate_of IS NULL")
                )
            ).scalar_one()
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        response.status_code = 503
        return {"status": "not ready", "checks": checks}
    return {"status": "ready", "checks": checks}


@ops.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- demo key -------------------------------------------------------------------------
class DemoKey(BaseModel):
    api_key: str
    expires_in_hours: int = 24
    note: str = "Demo key with strict rate limits. Shown once; keep it private."


@router.post("/demo-key", response_model=DemoKey, tags=["auth"])
async def demo_key(request: Request) -> DemoKey:
    if not get_settings().demo_key_enabled:
        raise HTTPException(404, "demo keys are disabled")
    # At most 3 demo keys per client IP per hour.
    _limit(request, f"ip:{_client_ip(request)}", 3, 3 / 3600, 1, "demo_key")
    return DemoKey(api_key=await create_api_key("demo", is_demo=True))


# --- search & postings -------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field("", max_length=500)
    filters: SearchFilters | None = None
    top_k: int = Field(20, ge=1, le=50)
    mode: Literal["fts", "dense", "hybrid", "hybrid_rerank"] = "hybrid_rerank"


class SearchResponse(BaseModel):
    query: str
    mode: str
    results: list[S.PostingSummary]
    took_ms: float


@router.post("/search", response_model=SearchResponse, tags=["search"])
async def search(body: SearchRequest, _: Auth) -> SearchResponse:
    t0 = time.perf_counter()
    if body.query.strip():
        hits = await hybrid_search(body.query, body.filters, top_k=body.top_k, mode=body.mode)
        mode: str = body.mode
    else:
        hits = await browse(body.filters, limit=body.top_k)
        mode = "browse"
    return SearchResponse(
        query=body.query,
        mode=mode,
        results=[summary_from_hit(h) for h in hits],
        took_ms=round((time.perf_counter() - t0) * 1000, 1),
    )


@router.get("/postings/{posting_id}", response_model=S.PostingDetail, tags=["search"])
async def get_posting(posting_id: str, _: Auth) -> S.PostingDetail:
    async with session_scope() as s:
        p = await s.get(Posting, posting_id)
    if p is None:
        raise HTTPException(404, "posting not found")
    # No excerpt: the API never republishes posting texts.
    return detail_from_row(p, with_excerpt=False)


# --- chat (SSE) -------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


def _sse(event: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps(data, ensure_ascii=False, default=str), event=event)


@router.post("/chat", tags=["assistant"], response_class=EventSourceResponse)
async def chat(body: ChatRequest, request: Request, principal: ChatAuth) -> EventSourceResponse:
    """Server-Sent Events: `token`, `tool_call`, `tool_result`, `citation`, `reset`, `done`
    (and `error`). `done` carries the final checked answer, citations, trace_id."""
    agent = request.app.state.agent
    session_id = body.session_id or uuid.uuid4().hex
    message_id = uuid.uuid4().hex
    from jobscout.observability import current_trace_id

    async def events() -> AsyncIterator[ServerSentEvent]:
        t0 = time.perf_counter()
        first = True
        try:
            async for ev in agent.astream_events(body.message, session_id):
                etype = ev.get("type", "message")
                if etype == "token" and first:
                    TIME_TO_FIRST_TOKEN.observe(time.perf_counter() - t0)
                    first = False
                if etype == "done":
                    ev = {
                        **ev,
                        "message_id": message_id,
                        "trace_id": current_trace_id() or request.state.request_id,
                    }
                    ev.pop("tool_results", None)
                payload = {k: v for k, v in ev.items() if k != "type"}
                yield _sse(etype, payload)
        except Exception as exc:  # stream an error event instead of a broken stream
            log.error("chat_failed", error=type(exc).__name__)
            yield _sse("error", {"message": "The assistant failed to answer. Please try again."})

    return EventSourceResponse(events(), ping=15, headers={"X-Session-Id": session_id})


# --- CV match -----------------------------------------------------------------------
class CvMatchResponse(BaseModel):
    profile: S.CandidateProfile
    matches: list[S.CvMatch]
    stored_profile_id: str | None = None
    privacy: str = "Your CV was processed in memory and not stored."


@router.post("/cv/match", response_model=CvMatchResponse, tags=["cv"])
async def cv_match(
    principal: Annotated[Principal, Depends(require_key(cost=3.0))],
    file: Annotated[UploadFile | None, File(description="PDF or .txt CV, max 2 MB")] = None,
    cv_text: Annotated[str | None, Form(max_length=20000)] = None,
    store_profile: Annotated[
        bool, Form(description="Opt in to store the structured profile")
    ] = False,
    city: Annotated[str | None, Form()] = None,
    top_k: Annotated[int, Form(ge=1, le=20)] = 8,
) -> CvMatchResponse:
    from jobscout.cv.match import match_profile
    from jobscout.cv.parse import CvParseError, extract_text
    from jobscout.cv.profile import extract_profile
    from jobscout.cv.store import save_profile

    try:
        if file is not None:
            data = await file.read(2 * 1024 * 1024 + 1)
            text_ = extract_text(file.filename or "cv", data, file.content_type)
        elif cv_text:
            text_ = extract_text("cv.txt", cv_text.encode("utf-8"), "text/plain")
        else:
            raise HTTPException(422, "upload a file or send cv_text")
    except CvParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        profile = await extract_profile(text_)
    except ValueError as exc:
        raise HTTPException(422, "could not read a profile from this CV") from exc
    finally:
        del text_  # drop the CV text as early as possible
    result = await match_profile(profile, SearchFilters(city=city) if city else None, top_k)
    stored = None
    privacy = "Your CV was processed in memory and not stored."
    if store_profile and get_settings().cv_store_opt_in_allowed:
        stored = str(await save_profile(profile, _key_id(principal)))
        privacy = (
            "You opted in: only the structured profile (no CV text) was stored. Delete it any time."
        )
    return CvMatchResponse(
        profile=profile, matches=result.matches, stored_profile_id=stored, privacy=privacy
    )


@router.delete("/cv/profiles/{profile_id}", status_code=204, tags=["cv"])
async def delete_cv_profile(profile_id: uuid.UUID, principal: Auth) -> Response:
    from jobscout.cv.store import delete_profile

    if not await delete_profile(profile_id, _key_id(principal)):
        raise HTTPException(404, "profile not found")
    return Response(status_code=204)


# --- saved searches -----------------------------------------------------------------
class SavedSearchIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    query: str = Field("", max_length=500)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class SavedSearchOut(SavedSearchIn):
    id: uuid.UUID
    feed_url: str
    atom_url: str


def _saved_out(row: SavedSearch) -> SavedSearchOut:
    base = get_settings().public_base_url.rstrip("/")
    return SavedSearchOut(
        id=row.id,
        name=row.name,
        query=row.query or "",
        filters=SearchFilters(**(row.filters or {})),
        feed_url=f"{base}/api/feeds/{row.id}.xml",
        atom_url=f"{base}/api/feeds/{row.id}.atom",
    )


@router.post("/saved-searches", response_model=SavedSearchOut, status_code=201, tags=["feeds"])
async def create_saved_search(body: SavedSearchIn, principal: Auth) -> SavedSearchOut:
    async with session_scope() as s:
        row = SavedSearch(
            name=body.name,
            query=body.query,
            filters=body.filters.model_dump(exclude_none=True),
            api_key_id=_key_id(principal),
        )
        s.add(row)
        await s.commit()
        return _saved_out(row)


@router.get("/saved-searches", response_model=list[SavedSearchOut], tags=["feeds"])
async def list_saved_searches(principal: Auth) -> list[SavedSearchOut]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(SavedSearch)
                    .where(SavedSearch.api_key_id.is_not_distinct_from(_key_id(principal)))
                    .order_by(SavedSearch.created_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [_saved_out(r) for r in rows]


@router.delete("/saved-searches/{saved_id}", status_code=204, tags=["feeds"])
async def delete_saved_search(saved_id: uuid.UUID, principal: Auth) -> Response:
    async with session_scope() as s:
        res = await s.execute(
            delete(SavedSearch).where(
                SavedSearch.id == saved_id,
                SavedSearch.api_key_id.is_not_distinct_from(_key_id(principal)),
            )
        )
        await s.commit()
    if not getattr(res, "rowcount", 0):
        raise HTTPException(404, "saved search not found")
    return Response(status_code=204)


# --- feedback -------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    trace_id: str | None = Field(None, max_length=64)
    session_id: str | None = Field(None, max_length=64)
    message_id: str | None = Field(None, max_length=64)
    question: str = Field(..., max_length=2000)
    answer: str = Field(..., max_length=8000)
    rating: Literal[-1, 1]
    comment: str | None = Field(None, max_length=2000)


class FeedbackOut(BaseModel):
    id: int
    status: str = "recorded"


@router.post("/feedback", response_model=FeedbackOut, status_code=201, tags=["assistant"])
async def feedback(body: FeedbackIn, principal: Auth) -> FeedbackOut:
    async with session_scope() as s:
        row = Feedback(**body.model_dump(), api_key_id=_key_id(principal))
        s.add(row)
        await s.commit()
        FEEDBACK.labels(rating="up" if body.rating > 0 else "down").inc()
        return FeedbackOut(id=row.id)


@router.get("/meta", tags=["meta"])
async def meta() -> dict[str, Any]:
    """Public metadata for the UI footer: sources, attribution, disclaimer."""
    from jobscout.bluecard import load_config

    cfg = load_config()
    th, _ = cfg.for_year(time.gmtime().tm_year)
    return {
        "version": __version__,
        "attribution": [
            {"name": "Arbeitnow", "url": "https://www.arbeitnow.com"},
            {
                "name": "Bundesagentur für Arbeit – Jobsuche",
                "url": "https://www.arbeitsagentur.de/jobsuche/",
            },
        ],
        "bluecard": {
            "year": th.year,
            "standard": th.standard,
            "reduced": th.reduced,
            "source_url": th.source_url,
            "disclaimer": cfg.disclaimer,
        },
    }
