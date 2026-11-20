"""Contract checks (blocking, severity ERROR): when one fails, the assets downstream of
the checked asset do not run in that run, so bad rows never reach search, insights or
the snapshot. The row-level rules are plain functions, tested on their own."""

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

import dagster as dg
from pydantic import ValidationError

from jobscout.orchestration.assets.pipeline import (
    extracted_fields,
    posting_embeddings,
    raw_postings,
    salary_bluecard,
)
from jobscout.orchestration.assets.snapshot import postings_snapshot
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings
from jobscout.schema import JobPosting
from jobscout.tools.schemas import POSTING_ID_PATTERN

SAMPLE = 10  # failing IDs shown in the check metadata
REQUIRED = ("id", "source", "source_id", "url", "title", "raw_text", "content_hash")
REQUIRED_SQL = (
    "SELECT id, source, source_id, url, title, raw_text, content_hash FROM postings WHERE is_active"
)
CURRENCIES = {"EUR"}
PERIODS = {"year", "month", "hour"}
# Annualised EUR pay outside this range is a parsing error, not a real offer. The floor is
# low on purpose: mandatory internships ("Pflichtpraktikum") pay a few hundred EUR a month.
ANNUAL_EUR_RANGE = (1_000.0, 1_000_000.0)

_ID_RE = re.compile(POSTING_ID_PATTERN)


def duplicate_ids(ids: Iterable[str]) -> list[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def malformed_ids(ids: Iterable[str]) -> list[str]:
    return sorted(i for i in ids if not _ID_RE.match(i or ""))


def missing_required(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [str(r.get("id")) for r in rows if any(not r.get(k) for k in REQUIRED)]


def to_job_posting(r: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the extraction schema object from the stored columns."""
    has_salary = r.get("salary_min") is not None or r.get("salary_max") is not None
    return {
        "title": r["title"],
        "company": r.get("company"),
        "locations": r.get("locations") or [],
        "remote_policy": r["remote_policy"],
        "seniority": r["seniority"],
        "min_years_experience": r.get("min_years_experience"),
        "required_skills": r.get("required_skills") or [],
        "nice_to_have_skills": r.get("nice_to_have_skills") or [],
        "languages_required": r.get("languages_required") or [],
        "english_sufficient": r["english_sufficient"],
        "contract_type": r["contract_type"],
        "contract_duration_months": r.get("contract_duration_months"),
        "salary": (
            {
                "min": r.get("salary_min"),
                "max": r.get("salary_max"),
                "currency": r.get("salary_currency") or "EUR",
                "period": r.get("salary_period") or "year",
            }
            if has_salary
            else None
        ),
        "visa_sponsorship": r["visa_sponsorship"],
        "relocation_support": r["relocation_support"],
        "application_deadline": r.get("application_deadline"),
        "posting_language": r["posting_language"],
    }


def invalid_extractions(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    bad = []
    for r in rows:
        try:
            JobPosting.model_validate(to_job_posting(r))
        except (ValidationError, KeyError) as exc:
            first = exc.errors()[0] if isinstance(exc, ValidationError) else {"loc": exc.args}
            bad.append((str(r["id"]), ".".join(map(str, first["loc"]))))
    return bad


def salary_problems(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    bad = []
    lo, hi = ANNUAL_EUR_RANGE
    for r in rows:
        smin, smax = r.get("salary_min"), r.get("salary_max")
        if not r.get("salary_stated"):
            if smin is not None or smax is not None:
                bad.append((r["id"], "amount without salary_stated"))
            continue
        if smin is None and smax is None:
            bad.append((r["id"], "salary_stated without an amount"))
        elif smin is not None and smax is not None and smin > smax:
            bad.append((r["id"], "min > max"))
        elif r.get("salary_currency") not in CURRENCIES:
            bad.append((r["id"], f"currency {r.get('salary_currency')}"))
        elif r.get("salary_period") not in PERIODS:
            bad.append((r["id"], f"period {r.get('salary_period')}"))
        else:
            for col in ("annual_salary_min_eur", "annual_salary_max_eur"):
                v = r.get(col)
                if v is not None and not lo <= v <= hi:
                    bad.append((r["id"], f"{col}={v:.0f} outside plausible range"))
                    break
    return bad


def _result(failures: list[Any], checked: int, **extra: Any) -> dg.AssetCheckResult:
    return dg.AssetCheckResult(
        passed=not failures,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "checked": checked,
            "failures": len(failures),
            "sample": str(failures[:SAMPLE]),
            **extra,
        },
    )


@dg.asset_check(
    asset=raw_postings,
    blocking=True,
    description="Posting IDs follow the ID contract and are unique; (source, source_id) is unique.",
)
def posting_ids_unique(db: JobScoutDb) -> dg.AssetCheckResult:
    rows = db.fetch_all("SELECT id, source, source_id FROM postings")
    ids = [r["id"] for r in rows]
    keys = [f"{r['source']}:{r['source_id']}" for r in rows]
    failures = [f"duplicate {i}" for i in duplicate_ids(ids)]
    failures += [f"duplicate key {k}" for k in duplicate_ids(keys)]
    failures += [f"malformed {i}" for i in malformed_ids(ids)]
    return _result(failures, len(rows))


@dg.asset_check(
    asset=raw_postings, blocking=True, description="Required posting fields are not null or empty."
)
def required_fields_present(db: JobScoutDb) -> dg.AssetCheckResult:
    rows = db.fetch_all(REQUIRED_SQL)
    return _result(missing_required(rows), len(rows), required=", ".join(REQUIRED))


@dg.asset_check(
    asset=extracted_fields,
    blocking=True,
    description="Every extracted posting validates against the JobPosting Pydantic schema.",
)
def extractions_match_schema(db: JobScoutDb) -> dg.AssetCheckResult:
    rows = db.fetch_all(
        "SELECT * FROM postings WHERE extraction_status = 'ok' AND is_active AND duplicate_of IS NULL"
    )
    return _result(invalid_extractions(rows), len(rows))


@dg.asset_check(
    asset=salary_bluecard,
    blocking=True,
    description="Salary fields are consistent: min <= max, EUR, a known period and a plausible annual amount.",
)
def salary_fields_consistent(db: JobScoutDb) -> dg.AssetCheckResult:
    rows = db.fetch_all(
        "SELECT id, salary_stated, salary_min, salary_max, salary_currency, salary_period, "
        "annual_salary_min_eur, annual_salary_max_eur FROM postings WHERE is_active AND duplicate_of IS NULL"
    )
    return _result(salary_problems(rows), len(rows))


@dg.asset_check(
    asset=posting_embeddings,
    blocking=True,
    description="Every active posting has a vector of the configured dimension.",
)
def embeddings_complete(db: JobScoutDb, jobscout: JobScoutSettings) -> dg.AssetCheckResult:
    dim = jobscout.app.embed_dim
    rows = db.fetch_all(
        "SELECT id, CASE WHEN embedding IS NULL THEN 0 ELSE vector_dims(embedding) END AS dims "
        "FROM postings WHERE is_active AND duplicate_of IS NULL"
    )
    failures = [(r["id"], int(r["dims"])) for r in rows if int(r["dims"]) != dim]
    return _result(failures, len(rows), expected_dim=dim)


@dg.asset_check(
    asset=postings_snapshot,
    blocking=True,
    description="Posting IDs in the latest Parquet snapshot are unique and well-formed.",
)
def snapshot_ids_unique(jobscout: JobScoutSettings) -> dg.AssetCheckResult:
    from jobscout.orchestration.checks.snapshot_reader import latest_snapshot

    path, rows = latest_snapshot(jobscout.snapshot_path, ["id"])
    ids = [r["id"] for r in rows]
    failures = [f"duplicate {i}" for i in duplicate_ids(ids)] + [
        f"malformed {i}" for i in malformed_ids(ids)
    ]
    return _result(failures, len(ids), file=str(path))


CONTRACT_CHECKS = [
    posting_ids_unique,
    required_fields_present,
    extractions_match_schema,
    salary_fields_consistent,
    embeddings_complete,
    snapshot_ids_unique,
]
