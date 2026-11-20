"""Privacy checks (blocking, severity ERROR). The rules are written down in docs/ORCHESTRATION.md.

1. ``no_cv_text_stored``: CVs are processed in memory. The check proves that the database
   holds no CV text: no column anywhere is named like a CV/resume text field, the LLM cache
   only holds posting extractions, and the opt-in ``cv_profiles`` table (structured
   profiles a user chose to keep) contains only the allowed profile keys and no contact data.
2. ``snapshot_contacts_masked``: posting text in the Parquet snapshot carries no e-mail
   address or phone number (``masking.find_contacts``).
"""

import json
import re
from typing import Any

import dagster as dg

from jobscout.orchestration.assets.pipeline import raw_postings
from jobscout.orchestration.assets.snapshot import postings_snapshot
from jobscout.orchestration.masking import find_contacts
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings
from jobscout.tools.schemas import CandidateProfile

SAMPLE = 10
CV_COLUMN_RE = re.compile(r"(^|_)(cv|resume|lebenslauf|curriculum)(_|$)", re.IGNORECASE)
ALLOWED_PROFILE_KEYS = frozenset(CandidateProfile.model_fields)
ALLOWED_LLM_CACHE_KINDS = frozenset({"extract"})

COLUMNS_SQL = """
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'public' ORDER BY table_name, column_name"""


def cv_text_columns(columns: list[dict[str, Any]]) -> list[str]:
    """Columns that look like a place for CV text. ``cv_profiles.profile`` (structured,
    opt-in) is the one allowed CV-related column."""
    out = []
    for c in columns:
        name = f"{c['table_name']}.{c['column_name']}"
        if name == "cv_profiles.profile" or c["table_name"] == "cv_profiles":
            continue
        if CV_COLUMN_RE.search(c["column_name"]):
            out.append(name)
    return out


def profile_problems(profiles: list[dict[str, Any]]) -> list[str]:
    problems = []
    for p in profiles:
        profile = p["profile"] if isinstance(p["profile"], dict) else json.loads(p["profile"])
        extra = set(profile) - ALLOWED_PROFILE_KEYS
        if extra:
            problems.append(f"{p['id']}: unexpected keys {sorted(extra)}")
        if find_contacts(json.dumps(profile, ensure_ascii=False)):
            problems.append(f"{p['id']}: contact data")
    return problems


@dg.asset_check(
    asset=raw_postings,
    blocking=True,
    description="The database stores no CV text: no CV text columns, no CV prompts in the LLM cache, opt-in profiles hold only structured keys.",
)
def no_cv_text_stored(db: JobScoutDb) -> dg.AssetCheckResult:
    columns = cv_text_columns(db.fetch_all(COLUMNS_SQL))
    kinds = {r["kind"] for r in db.fetch_all("SELECT DISTINCT kind FROM llm_cache")}
    profiles = db.fetch_all("SELECT id, profile FROM cv_profiles")
    failures = [f"column {c}" for c in columns]
    failures += [f"llm_cache kind {k}" for k in sorted(kinds - ALLOWED_LLM_CACHE_KINDS)]
    failures += profile_problems(profiles)
    return dg.AssetCheckResult(
        passed=not failures,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "failures": len(failures),
            "sample": str(failures[:SAMPLE]),
            "opt_in_profiles": len(profiles),
            "llm_cache_kinds": ", ".join(sorted(kinds)) or "none",
        },
    )


@dg.asset_check(
    asset=postings_snapshot,
    blocking=True,
    description="Posting text in the latest snapshot has no unmasked e-mail address or phone number.",
)
def snapshot_contacts_masked(jobscout: JobScoutSettings) -> dg.AssetCheckResult:
    from jobscout.orchestration.checks.snapshot_reader import latest_snapshot

    path, rows = latest_snapshot(jobscout.snapshot_path, ["id", "text_masked"])
    leaks = [r["id"] for r in rows if find_contacts(r["text_masked"] or "")]
    return dg.AssetCheckResult(
        passed=not leaks,
        severity=dg.AssetCheckSeverity.ERROR,
        # IDs only: the leaked values themselves must not be copied into run metadata.
        metadata={
            "checked": len(rows),
            "postings_with_contacts": len(leaks),
            "sample_ids": str(leaks[:SAMPLE]),
            "file": str(path),
        },
    )


PRIVACY_CHECKS = [no_cv_text_stored, snapshot_contacts_masked]
