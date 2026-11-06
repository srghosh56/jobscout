"""`make salary-refresh` corrects stored salaries and leaves every other column alone."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.extract.runner import refresh_salaries
from jobscout.ingest.cleaning import CleanPosting
from jobscout.ingest.upsert import upsert_postings

pytestmark = pytest.mark.db


def make(sid: str, text: str) -> CleanPosting:
    return CleanPosting(
        source="arbeitnow",
        source_id=sid,
        url=f"https://example.com/{sid}",
        title="Software Engineer",
        company="Fictional GmbH",
        raw_text=text,
        city="Berlin",
        cities=["Berlin"],
    )


async def stored(sid: str) -> Posting:
    async with session_scope() as s:
        return (await s.execute(select(Posting).where(Posting.source_id == sid))).scalar_one()


async def test_refresh_fixes_salary_and_blue_card_only(clean_db: None) -> None:
    async with session_scope() as s:
        await upsert_postings(
            s,
            [
                make("wrong-period", "Gehalt: 60.000 € pro Jahr bei 40 Stunden pro Woche."),
                make("perk", "Great team and an annual learning budget of €2,000."),
            ],
        )
        await s.execute(
            update(Posting).values(
                extraction_status="ok",
                seniority="senior",
                salary_stated=True,
                salary_currency="EUR",
            )
        )
        await s.execute(
            update(Posting)
            .where(Posting.source_id == "wrong-period")
            .values(
                salary_min=60000,
                salary_period="hour",
                annual_salary_min_eur=124800000,
                bluecard_status="meets_standard",
            )
        )
        await s.execute(
            update(Posting)
            .where(Posting.source_id == "perk")
            .values(
                salary_min=2000,
                salary_period="year",
                annual_salary_min_eur=2000,
                bluecard_status="below",
            )
        )
        await s.commit()

    assert await refresh_salaries() == {"checked": 2, "changed": 2}

    fixed = await stored("wrong-period")
    assert fixed.salary_period == "year" and fixed.annual_salary_min_eur == 60000
    assert fixed.bluecard_status == "meets_standard" and fixed.seniority == "senior"

    perk = await stored("perk")
    assert perk.salary_stated is False and perk.salary_min is None
    assert perk.bluecard_status == "salary_not_stated" and perk.seniority == "senior"

    assert await refresh_salaries() == {"checked": 2, "changed": 0}
