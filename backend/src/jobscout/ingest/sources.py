"""Clients for the two job-board APIs (endpoints verified against the live APIs on 2026-11-02)."""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

from jobscout.ingest.cleaning import (
    CleanPosting,
    classify_country,
    html_to_text,
    normalize_city,
    strip_boilerplate,
)
from jobscout.ingest.http import OfflineCacheMiss, PoliteClient
from jobscout.logging import get_logger

log = get_logger(__name__)

MIN_TEXT_CHARS = 300
IN_SCOPE_COUNTRIES = ("Germany", None)

# ---------------------------------------------------------------------------
# Bundesagentur für Arbeit - Jobsuche API (unofficial, documented by bundesAPI)
# ---------------------------------------------------------------------------
BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_SEARCH = f"{BA_BASE}/pc/v6/jobs"
BA_DETAILS = f"{BA_BASE}/pc/v4/jobdetails/{{code}}"
# Public client id documented by bundesAPI/jobsuche-api; not a secret.
BA_HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
BA_PUBLIC_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"

DEFAULT_BA_QUERIES = [
    "Data Scientist",
    "Machine Learning Engineer",
    "Data Engineer",
    "Data Analyst",
    "Softwareentwickler",
    "Software Engineer",
    "Backend Developer",
    "Frontend Developer",
    "DevOps Engineer",
    "Cloud Engineer",
    "IT Security",
    "Product Manager",
]
DEFAULT_BA_CITIES = [
    "Berlin",
    "München",
    "Hamburg",
    "Frankfurt am Main",
    "Köln",
    "Stuttgart",
    "Düsseldorf",
    "Leipzig",
]


@dataclass
class BAConfig:
    queries: list[str]
    cities: list[str]
    radius_km: int = 25
    page_size: int = 50
    max_pages: int = 2
    published_within_days: int = 28


def encode_refnr(refnr: str) -> str:
    return base64.b64encode(refnr.encode()).decode()


def _parse_date(value: Any) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def ba_detail_to_posting(refnr: str, hit: dict[str, Any], detail: dict[str, Any]) -> CleanPosting:
    locs = detail.get("stellenlokationen") or hit.get("stellenlokationen") or []
    cities: list[str] = []
    for loc in locs:
        city = normalize_city((loc.get("adresse") or {}).get("ort"))
        if city and city not in cities:
            cities.append(city)
    text = html_to_text(str(detail.get("stellenangebotsBeschreibung") or ""))
    return CleanPosting(
        source="arbeitsagentur",
        source_id=refnr,
        url=BA_PUBLIC_URL.format(refnr=refnr),
        title=str(detail.get("stellenangebotsTitel") or hit.get("stellenangebotsTitel") or ""),
        company=detail.get("firma") or hit.get("firma"),
        raw_text=text,
        city=cities[0] if cities else None,
        cities=cities,
        published_at=_parse_date((hit.get("veroeffentlichungszeitraum") or {}).get("von"))
        or _parse_date(hit.get("datumErsteVeroeffentlichung")),
        source_remote_flag=bool(hit.get("homeofficemoeglich")) or None,
        extra={
            "hauptberuf": hit.get("hauptberuf"),
            "external_url": hit.get("externeURL"),
            "vertragsdauer": detail.get("vertragsdauer"),
        },
    )


async def fetch_arbeitsagentur(
    client: PoliteClient, cfg: BAConfig, limit: int | None = None
) -> AsyncIterator[CleanPosting]:
    seen: set[str] = set()
    emitted = 0
    for query in cfg.queries:
        for city in cfg.cities:
            for page in range(1, cfg.max_pages + 1):
                params = {
                    "was": query,
                    "wo": city,
                    "umkreis": cfg.radius_km,
                    "angebotsart": 1,
                    "veroeffentlichtseit": cfg.published_within_days,
                    "page": page,
                    "size": cfg.page_size,
                }
                try:
                    data = await client.get_json(BA_SEARCH, params=params, headers=BA_HEADERS)
                except OfflineCacheMiss:
                    break
                hits = data.get("ergebnisliste") or []
                for hit in hits:
                    refnr = hit.get("referenznummer") or hit.get("refnr")
                    if not refnr or refnr in seen:
                        continue
                    seen.add(refnr)
                    try:
                        detail = await client.get_json(
                            BA_DETAILS.format(code=encode_refnr(refnr)), headers=BA_HEADERS
                        )
                    except OfflineCacheMiss:
                        continue
                    except Exception as exc:  # one bad detail must not stop the run
                        log.warning("ba_detail_failed", refnr=refnr, error=type(exc).__name__)
                        continue
                    posting = ba_detail_to_posting(refnr, hit, detail)
                    if len(posting.raw_text) < MIN_TEXT_CHARS:
                        continue
                    yield posting
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return
                if len(hits) < cfg.page_size:
                    break


# ---------------------------------------------------------------------------
# Arbeitnow Job Board API (free, public; link back required)
# ---------------------------------------------------------------------------
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"


def arbeitnow_to_posting(job: dict[str, Any]) -> CleanPosting:
    location = str(job.get("location") or "")
    country = classify_country(location)
    city = normalize_city(location) if country == "Germany" else None
    created = job.get("created_at")
    published = (
        dt.datetime.fromtimestamp(int(created), tz=dt.UTC).date()
        if isinstance(created, int | float) or (isinstance(created, str) and created.isdigit())
        else None
    )
    text = strip_boilerplate(html_to_text(str(job.get("description") or "")))
    return CleanPosting(
        source="arbeitnow",
        source_id=str(job["slug"]),
        url=str(job.get("url") or ""),
        title=str(job.get("title") or ""),
        company=job.get("company_name"),
        raw_text=text,
        city=city,
        cities=[city] if city else [],
        published_at=published,
        country=country,
        source_remote_flag=bool(job.get("remote")),
        extra={
            "location": location,
            "tags": job.get("tags") or [],
            "job_types": job.get("job_types") or [],
        },
    )


async def fetch_arbeitnow(
    client: PoliteClient, max_pages: int = 15, limit: int | None = None
) -> AsyncIterator[CleanPosting]:
    emitted = 0
    for page in range(1, max_pages + 1):
        try:
            data = await client.get_json(ARBEITNOW_URL, params={"page": page})
        except OfflineCacheMiss:
            break
        jobs: Iterable[dict[str, Any]] = data.get("data") or []
        count = 0
        for job in jobs:
            count += 1
            if not job.get("slug"):
                continue
            posting = arbeitnow_to_posting(job)
            if posting.country not in IN_SCOPE_COUNTRIES:
                continue  # product scope: jobs in Germany (remote/unknown kept)
            if len(posting.raw_text) < MIN_TEXT_CHARS:
                continue
            yield posting
            emitted += 1
            if limit is not None and emitted >= limit:
                return
        if count == 0 or not (data.get("links") or {}).get("next"):
            break
