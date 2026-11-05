"""Public, key-less routes: insights (F5) and feeds (F7, unguessable UUID in the URL so
feed readers work without headers). Both are rate-limited per client IP."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from jobscout.api.routes import _client_ip, _limit
from jobscout.feeds import build_feed
from jobscout.insights import get_insights

public = APIRouter(prefix="/api")


@public.get("/insights", tags=["insights"])
async def insights(request: Request) -> dict[str, Any]:
    _limit(request, f"ip-insights:{_client_ip(request)}", 60, 1.0, 1, "public")
    return await get_insights()


@public.get("/feeds/{saved_id}.xml", tags=["feeds"], response_class=Response)
async def feed_rss(saved_id: uuid.UUID, request: Request) -> Response:
    _limit(request, f"ip-feeds:{_client_ip(request)}", 30, 0.5, 1, "public")
    body = await build_feed(saved_id, "rss")
    if body is None:
        raise HTTPException(404, "feed not found")
    return Response(
        body,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=900"},
    )


@public.get("/feeds/{saved_id}.atom", tags=["feeds"], response_class=Response)
async def feed_atom(saved_id: uuid.UUID, request: Request) -> Response:
    _limit(request, f"ip-feeds:{_client_ip(request)}", 30, 0.5, 1, "public")
    body = await build_feed(saved_id, "atom")
    if body is None:
        raise HTTPException(404, "feed not found")
    return Response(
        body,
        media_type="application/atom+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=900"},
    )
