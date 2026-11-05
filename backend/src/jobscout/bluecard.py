"""EU Blue Card salary check (F2). Thresholds live in config/bluecard.yaml with the
official source; this module only annualises and compares. Always indicative."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from jobscout.settings import CONFIG_DIR

Status = Literal["meets_standard", "meets_reduced_only", "below", "salary_not_stated"]
STATUSES: tuple[Status, ...] = (
    "meets_standard",
    "meets_reduced_only",
    "below",
    "salary_not_stated",
)


@dataclass(frozen=True)
class Threshold:
    year: int
    currency: str
    standard: float
    reduced: float
    reduced_applies_to: str
    source_title: str
    source_url: str


@dataclass(frozen=True)
class BlueCardConfig:
    thresholds: dict[int, Threshold]
    months_per_year: int
    hours_per_year: float
    hours_note: str
    disclaimer: str

    def for_year(self, year: int) -> tuple[Threshold, bool]:
        """Threshold for ``year``; falls back to the latest known year (flag=True)."""
        if year in self.thresholds:
            return self.thresholds[year], False
        latest = max(self.thresholds)
        return self.thresholds[latest], True


@lru_cache(maxsize=4)
def load_config(path: Path = CONFIG_DIR / "bluecard.yaml") -> BlueCardConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    th = {
        int(t["year"]): Threshold(
            year=int(t["year"]),
            currency=t["currency"],
            standard=float(t["standard"]),
            reduced=float(t["reduced"]),
            reduced_applies_to=t["reduced_applies_to"],
            source_title=t["source_title"],
            source_url=t["source_url"],
        )
        for t in data["thresholds"]
    }
    a = data["annualisation"]
    return BlueCardConfig(
        thresholds=th,
        months_per_year=int(a["months_per_year"]),
        hours_per_year=float(a["hours_per_year"]),
        hours_note=a["hours_assumption_note"],
        disclaimer=data["disclaimer"],
    )


def annualise(amount: float | None, period: str | None, cfg: BlueCardConfig) -> float | None:
    if amount is None:
        return None
    if period == "month":
        return amount * cfg.months_per_year
    if period == "hour":
        return amount * cfg.hours_per_year
    return amount


@dataclass(frozen=True)
class BlueCardResult:
    status: Status
    annual_min_eur: float | None
    annual_max_eur: float | None
    threshold_year: int
    standard_threshold: float
    reduced_threshold: float
    explanation: str
    disclaimer: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(x: float) -> str:
    return f"{x:,.2f} EUR".replace(".00 EUR", " EUR")


def check(
    salary_min: float | None,
    salary_max: float | None,
    currency: str | None,
    period: str | None,
    year: int,
    cfg: BlueCardConfig | None = None,
) -> BlueCardResult:
    """Compare the *lower* end of the stated range (conservative) with the thresholds."""
    cfg = cfg or load_config()
    th, fallback = cfg.for_year(year)
    year_note = f" Thresholds for {year} are not configured; using {th.year}." if fallback else ""
    base: dict[str, Any] = {
        "threshold_year": th.year,
        "standard_threshold": th.standard,
        "reduced_threshold": th.reduced,
        "disclaimer": cfg.disclaimer,
        "source_url": th.source_url,
    }
    if (salary_min is None and salary_max is None) or (currency or "EUR").upper() != th.currency:
        reason = (
            "The posting does not state a salary."
            if salary_min is None and salary_max is None
            else f"The salary is stated in {currency}, not {th.currency}; no conversion is attempted."
        )
        return BlueCardResult(
            status="salary_not_stated",
            annual_min_eur=None,
            annual_max_eur=None,
            explanation=reason + " Ask the employer for the gross annual salary." + year_note,
            **base,
        )
    lo = annualise(salary_min if salary_min is not None else salary_max, period, cfg)
    hi = annualise(salary_max, period, cfg)
    assert lo is not None
    how = {
        "month": f"monthly amount × {cfg.months_per_year}",
        "hour": f"hourly rate × {cfg.hours_per_year:,.0f} h ({cfg.hours_note})",
    }.get(period or "year", "stated as annual")
    rng = f"{_fmt(lo)}" + (f"–{_fmt(hi)}" if hi is not None and hi != lo else "")
    if lo >= th.standard:
        status: Status = "meets_standard"
        verdict = f"meets the standard threshold of {_fmt(th.standard)}"
    elif lo >= th.reduced:
        status = "meets_reduced_only"
        verdict = (
            f"is below the standard threshold of {_fmt(th.standard)} but meets the reduced "
            f"threshold of {_fmt(th.reduced)}, which applies only to {th.reduced_applies_to}"
        )
    else:
        status = "below"
        verdict = (
            f"is below both thresholds ({_fmt(th.reduced)} reduced, {_fmt(th.standard)} standard)"
        )
        if hi is not None and hi >= th.reduced:
            verdict += "; the top of the range would qualify, so negotiate"
    explanation = (
        f"Annualised gross salary {rng} ({how}). The lower end {verdict} for {th.year}." + year_note
    )
    return BlueCardResult(
        status=status, annual_min_eur=lo, annual_max_eur=hi, explanation=explanation, **base
    )
