"""Table-driven Blue Card tests (thresholds from config/bluecard.yaml, 2026)."""

from __future__ import annotations

import pytest

from jobscout import bluecard as bc


@pytest.mark.parametrize(
    ("smin", "smax", "cur", "period", "status", "annual_min"),
    [
        (60000, 70000, "EUR", "year", "meets_standard", 60000),
        (50700, None, "EUR", "year", "meets_standard", 50700),  # exactly at threshold
        (50699.99, None, "EUR", "year", "meets_reduced_only", 50699.99),
        (45934.20, None, "EUR", "year", "meets_reduced_only", 45934.20),  # exactly reduced
        (45934.19, None, "EUR", "year", "below", 45934.19),
        (4300, 4600, "EUR", "month", "meets_standard", 51600),  # 4300*12
        (3900, None, "EUR", "month", "meets_reduced_only", 46800),
        (25, None, "EUR", "hour", "meets_standard", 52000),  # 25*2080
        (20, None, "EUR", "hour", "below", 41600),
        (None, 55000, "EUR", "year", "meets_standard", 55000),  # only max stated
        (None, None, None, None, "salary_not_stated", None),
        (80000, None, "USD", "year", "salary_not_stated", None),  # no FX conversion
        (40000, 50000, "EUR", "year", "below", 40000),  # range straddles → conservative
    ],
)
def test_check(
    smin: float | None,
    smax: float | None,
    cur: str | None,
    period: str | None,
    status: str,
    annual_min: float | None,
) -> None:
    r = bc.check(smin, smax, cur, period, 2026)
    assert r.status == status
    assert r.annual_min_eur == (pytest.approx(annual_min) if annual_min is not None else None)
    assert "not legal advice" in r.disclaimer.lower()
    assert r.source_url.startswith("https://www.bundesanzeiger.de/")


def test_thresholds_match_official_2026_values() -> None:
    th, fallback = bc.load_config().for_year(2026)
    assert (th.standard, th.reduced, fallback) == (50700.0, 45934.20, False)


def test_unknown_year_falls_back_and_says_so() -> None:
    r = bc.check(60000, None, "EUR", "year", 2031)
    assert r.threshold_year == 2026 and "not configured" in r.explanation


def test_explanations_are_specific() -> None:
    assert "negotiate" in bc.check(40000, 50000, "EUR", "year", 2026).explanation
    assert "× 12" in bc.check(4300, None, "EUR", "month", 2026).explanation
    assert "assum" in bc.check(30, None, "EUR", "hour", 2026).explanation.lower()
    assert "reduced" in bc.check(47000, None, "EUR", "year", 2026).explanation
    assert set(bc.STATUSES) == {
        "meets_standard",
        "meets_reduced_only",
        "below",
        "salary_not_stated",
    }
