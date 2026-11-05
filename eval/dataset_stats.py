"""Dataset statistics for EVALUATION.md (coverage, sources, extractor split) and the
rules-vs-LLM agreement on postings that have an LLM extraction."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.extract.extractor import derive_fields
from jobscout.extract.rules import rule_based_extract
from jobscout.schema import JobPosting

ROOT = Path(__file__).resolve().parent
FIELDS = [
    "english_sufficient",
    "german_level",
    "seniority",
    "remote_policy",
    "visa_sponsorship",
    "relocation_support",
    "salary_stated",
    "min_years_experience",
]


async def main() -> dict[str, Any]:
    async with session_scope() as s:
        base = (
            (
                await s.execute(
                    text(
                        """SELECT count(*) FILTER (WHERE duplicate_of IS NULL) AS unique_postings,
                      count(*) FILTER (WHERE duplicate_of IS NOT NULL) AS near_duplicates,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND is_active) AS active,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND NOT is_active) AS inactive,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND model_version LIKE 'ollama:%') AS llm_extracted,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND model_version LIKE 'rules:%') AS rules_extracted,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND extraction_status = 'failed') AS extraction_failed,
                      count(*) FILTER (WHERE duplicate_of IS NULL AND embedding IS NOT NULL) AS embedded
               FROM postings"""
                    )
                )
            )
            .one()
            ._asdict()
        )
        by_source = dict(
            (
                await s.execute(
                    text(
                        "SELECT source, count(*) FROM postings WHERE duplicate_of IS NULL GROUP BY 1"
                    )
                )
            ).all()
        )
        by_lang = dict(
            (
                await s.execute(
                    text(
                        "SELECT posting_language, count(*) FROM postings WHERE duplicate_of IS NULL GROUP BY 1"
                    )
                )
            ).all()
        )
        llm_rows = (
            (
                await s.execute(
                    select(Posting).where(
                        Posting.model_version.like("ollama:%"), Posting.duplicate_of.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    agree = dict.fromkeys(FIELDS, 0)
    for p in llm_rows:
        data = JobPosting.model_validate(rule_based_extract(f"{p.title}\n{p.raw_text}")).model_dump(
            mode="json"
        )
        rules = derive_fields(data, p.raw_text, 2026, p.source_remote_flag)
        for f in FIELDS:
            agree[f] += int(rules.get(f) == getattr(p, f))
    n = len(llm_rows)
    out = {
        **{k: int(v) for k, v in base.items()},
        "by_source": by_source,
        "by_posting_language": by_lang,
        "rules_vs_llm_agreement": {
            "n": n,
            **{f: round(agree[f] / n, 3) if n else None for f in FIELDS},
        },
        "note": "english_sufficient, german_level and visa/relocation are rule-derived in both paths, so their agreement is expected to be high; seniority/years/remote show what the LLM adds.",
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "dataset_stats.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    asyncio.run(main())
