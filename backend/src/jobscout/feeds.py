"""F7 RSS 2.0 / Atom feeds for saved searches (feedgen). Entries contain extracted
fields and a link to the original ad, never the posting text."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from feedgen.feed import FeedGenerator

from jobscout.db.engine import session_scope
from jobscout.db.models import SavedSearch
from jobscout.retrieval.filters import SearchFilters
from jobscout.retrieval.search import SearchHit, browse, hybrid_search
from jobscout.settings import get_settings

BLUECARD_LABEL = {
    "meets_standard": "meets standard threshold",
    "meets_reduced_only": "meets reduced threshold only",
    "below": "below threshold",
    "salary_not_stated": "salary not stated",
}


def entry_summary(h: SearchHit) -> str:
    parts = [
        f"{h.company or 'Unknown company'} · {h.city or 'location n/a'} · {h.remote_policy}",
        f"English enough: {h.english_sufficient}; German: {h.german_level}",
        f"Blue Card (indicative): {BLUECARD_LABEL.get(h.bluecard_status, h.bluecard_status)}",
    ]
    if h.required_skills:
        parts.append("Skills: " + ", ".join(h.required_skills[:8]))
    return " | ".join(parts)


async def build_feed(
    saved_id: uuid.UUID, fmt: Literal["rss", "atom"] = "rss", limit: int = 30
) -> bytes | None:
    async with session_scope() as s:
        row = await s.get(SavedSearch, saved_id)
    if row is None:
        return None
    filters = SearchFilters(**(row.filters or {}))
    if row.query:
        hits = await hybrid_search(row.query, filters, top_k=limit, mode="hybrid")
        hits.sort(key=lambda h: h.published_at or "", reverse=True)
    else:
        hits = await browse(filters, limit=limit)
    base = get_settings().public_base_url.rstrip("/")
    fg = FeedGenerator()
    fg.id(f"urn:jobscout:saved-search:{saved_id}")
    fg.title(f"JobScout: {row.name}")
    fg.link(href=f"{base}/search?saved={saved_id}", rel="alternate")
    fg.link(href=f"{base}/api/feeds/{saved_id}.{'xml' if fmt == 'rss' else 'atom'}", rel="self")
    fg.description(
        f"New postings for the saved search '{row.name}'. Blue Card status is indicative, not legal advice."
    )
    fg.language("en")
    now = dt.datetime.now(dt.UTC)
    for h in hits:
        fe = fg.add_entry(order="append")
        fe.id(f"urn:jobscout:posting:{h.id}")
        fe.title(f"{h.title} ({h.city or 'n/a'})")
        fe.link(href=h.url)
        fe.description(entry_summary(h))
        when = (
            dt.datetime.fromisoformat(h.published_at).replace(tzinfo=dt.UTC)
            if h.published_at
            else now
        )
        fe.published(when)
        fe.updated(when)
    return bytes(fg.rss_str(pretty=True) if fmt == "rss" else fg.atom_str(pretty=True))
