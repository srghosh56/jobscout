"""F5 market insights: materialised views refreshed after ingestion + the public JSON
payload with a methodology note. Cells with fewer than MIN_N postings are suppressed."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text

from jobscout.db.engine import session_scope
from jobscout.metrics import ACTIVE_POSTINGS

MIN_N = 5
TOP_CITIES = 10
MATVIEWS = ("mv_insights_city_family", "mv_top_skills", "mv_weekly_postings", "mv_weekly_rates")

METHODOLOGY = f"""Population: active, de-duplicated job postings from the Bundesagentur für Arbeit Jobsuche API
and the Arbeitnow API, collected weekly for selected skilled job families and large German cities.
It is a sample of what these two boards list, not the whole German job market.
"English-friendly" = english_sufficient is "yes": the ad does not require German at "good" level or above
and is written in English or says English is the working language (deterministic rule, see extract/rules.py).
"Salary stated" = the ad gives a concrete amount. "Visa/relocation mentioned" = the ad explicitly offers visa
sponsorship or relocation support. Fields are extracted by an LLM in JSON-schema mode or, where not yet
re-extracted, by deterministic rules (coverage is shown). Weeks use the publication date (or first-seen date).
Cells with fewer than {MIN_N} postings are suppressed. Numbers are recomputed after each weekly ingestion."""


async def refresh_insights() -> dict[str, Any]:
    async with session_scope() as s:
        for mv in MATVIEWS:
            await s.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
        await s.commit()
        n = (
            await s.execute(
                text("SELECT count(*) FROM postings WHERE is_active AND duplicate_of IS NULL")
            )
        ).scalar_one()
    ACTIVE_POSTINGS.set(n)
    return {"refreshed": list(MATVIEWS), "active_postings": n}


def _f(x: Any) -> float | None:
    return float(x) if x is not None else None


async def get_insights() -> dict[str, Any]:
    async with session_scope() as s:
        cov = (
            await s.execute(
                text(
                    """SELECT count(*) AS n_active,
                         count(*) FILTER (WHERE extraction_status = 'ok') AS n_extracted,
                         count(*) FILTER (WHERE extraction_status = 'ok' AND model_version NOT LIKE 'rules:%') AS n_llm,
                         count(*) FILTER (WHERE model_version LIKE 'rules:%') AS n_rules,
                         max(last_seen_at) AS last_ingested, max(last_checked_at) AS last_checked
                       FROM postings WHERE is_active AND duplicate_of IS NULL"""
                )
            )
        ).one()
        cities = (
            await s.execute(
                text(
                    """SELECT city, sum(n_active) AS n, sum(n_english_friendly) AS en,
                              sum(n_salary_stated) AS sal, sum(n_visa_or_relocation) AS visa
                       FROM mv_insights_city_family WHERE city <> 'unknown'
                       GROUP BY city HAVING sum(n_active) >= :m ORDER BY n DESC LIMIT :k"""
                ),
                {"m": MIN_N, "k": TOP_CITIES},
            )
        ).all()
        families = (
            await s.execute(
                text(
                    """SELECT job_family, sum(n_active) AS n, sum(n_english_friendly) AS en,
                              sum(n_salary_stated) AS sal, sum(n_visa_or_relocation) AS visa
                       FROM mv_insights_city_family GROUP BY job_family HAVING sum(n_active) >= :m
                       ORDER BY n DESC"""
                ),
                {"m": MIN_N},
            )
        ).all()
        city_family = (
            await s.execute(
                text(
                    """SELECT city, job_family, n_active, share_english_friendly FROM mv_insights_city_family
                       WHERE n_active >= :m AND city <> 'unknown' ORDER BY n_active DESC LIMIT 60"""
                ),
                {"m": MIN_N},
            )
        ).all()
        skills = (
            await s.execute(
                text(
                    "SELECT job_family, skill, n FROM mv_top_skills WHERE rnk <= 10 AND n >= :m ORDER BY job_family, rnk"
                ),
                {"m": MIN_N},
            )
        ).all()
        weekly = (
            await s.execute(
                text(
                    """SELECT r.week, coalesce(p.n_new, 0) AS n_new, r.n, r.share_english_friendly,
                              r.share_salary_stated, r.share_visa_or_relocation
                       FROM mv_weekly_rates r
                       LEFT JOIN (SELECT week, sum(n_new) AS n_new FROM mv_weekly_postings GROUP BY week) p USING (week)
                       WHERE r.n >= :m ORDER BY r.week DESC LIMIT 12"""
                ),
                {"m": MIN_N},
            )
        ).all()

    def rates(r: Any) -> dict[str, Any]:
        n = int(r.n)
        return {
            "n": n,
            "share_english_friendly": round(int(r.en) / n, 4),
            "share_salary_stated": round(int(r.sal) / n, 4),
            "share_visa_or_relocation": round(int(r.visa) / n, 4),
        }

    top_skills: dict[str, list[dict[str, Any]]] = {}
    for r in skills:
        top_skills.setdefault(r.job_family, []).append({"skill": r.skill, "n": int(r.n)})
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "coverage": {
            "active_postings": int(cov.n_active),
            "extracted": int(cov.n_extracted),
            "llm_extracted": int(cov.n_llm),
            "rules_extracted": int(cov.n_rules),
            "last_ingested": cov.last_ingested.isoformat() if cov.last_ingested else None,
            "last_checked": cov.last_checked.isoformat() if cov.last_checked else None,
        },
        "by_city": [{"city": r.city, **rates(r)} for r in cities],
        "by_job_family": [{"job_family": r.job_family, **rates(r)} for r in families],
        "city_family_english": [
            {
                "city": r.city,
                "job_family": r.job_family,
                "n": int(r.n_active),
                "share_english_friendly": _f(r.share_english_friendly),
            }
            for r in city_family
        ],
        "top_skills": top_skills,
        "weekly": [
            {
                "week": r.week.isoformat(),
                "new_postings": int(r.n_new),
                "n": int(r.n),
                "share_english_friendly": _f(r.share_english_friendly),
                "share_salary_stated": _f(r.share_salary_stated),
                "share_visa_or_relocation": _f(r.share_visa_or_relocation),
            }
            for r in reversed(weekly)
        ],
        "min_cell_size": MIN_N,
        "methodology": METHODOLOGY,
    }
