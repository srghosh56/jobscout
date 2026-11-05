"""insights materialised views (F5), refreshed after ingestion

Population for every view: active, non-duplicate, extracted postings in Germany.
Each view has a unique index so it can be refreshed CONCURRENTLY (no read lock).

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE = """
FROM postings
WHERE duplicate_of IS NULL AND extraction_status = 'ok' AND is_active
"""

VIEWS = {
    "mv_insights_city_family": f"""
        SELECT coalesce(city, 'unknown') AS city, coalesce(job_family, 'other') AS job_family,
               count(*) AS n_active,
               count(*) FILTER (WHERE english_sufficient = 'yes') AS n_english_friendly,
               round(avg((english_sufficient = 'yes')::int)::numeric, 4) AS share_english_friendly,
               count(*) FILTER (WHERE salary_stated) AS n_salary_stated,
               round(avg(salary_stated::int)::numeric, 4) AS share_salary_stated,
               count(*) FILTER (WHERE visa_sponsorship = 'yes' OR relocation_support = 'yes') AS n_visa_or_relocation,
               round(avg((visa_sponsorship = 'yes' OR relocation_support = 'yes')::int)::numeric, 4) AS share_visa_or_relocation
        {BASE}
        GROUP BY 1, 2
    """,
    "mv_top_skills": f"""
        WITH s AS (
            SELECT coalesce(job_family, 'other') AS job_family,
                   lower(trim(regexp_replace(skill, '\\s+(programming|development|skills?)$', '', 'i'))) AS skill
            {BASE.replace('FROM postings', 'FROM postings CROSS JOIN LATERAL unnest(required_skills) AS skill')}
        ), c AS (
            SELECT job_family, skill, count(*) AS n FROM s WHERE skill <> '' GROUP BY 1, 2
        )
        SELECT job_family, skill, n,
               rank() OVER (PARTITION BY job_family ORDER BY n DESC, skill) AS rnk
        FROM c
    """,
    "mv_weekly_postings": """
        SELECT date_trunc('week', coalesce(published_at::timestamp, first_seen_at))::date AS week,
               coalesce(city, 'unknown') AS city, coalesce(job_family, 'other') AS job_family,
               count(*) AS n_new
        FROM postings
        WHERE duplicate_of IS NULL
        GROUP BY 1, 2, 3
    """,
    "mv_weekly_rates": """
        SELECT date_trunc('week', coalesce(published_at::timestamp, first_seen_at))::date AS week,
               count(*) AS n,
               round(avg((english_sufficient = 'yes')::int)::numeric, 4) AS share_english_friendly,
               round(avg(salary_stated::int)::numeric, 4) AS share_salary_stated,
               round(avg((visa_sponsorship = 'yes' OR relocation_support = 'yes')::int)::numeric, 4) AS share_visa_or_relocation
        FROM postings
        WHERE duplicate_of IS NULL AND extraction_status = 'ok'
        GROUP BY 1
    """,
}
UNIQUE = {
    "mv_insights_city_family": "(city, job_family)",
    "mv_top_skills": "(job_family, skill)",
    "mv_weekly_postings": "(week, city, job_family)",
    "mv_weekly_rates": "(week)",
}


def upgrade() -> None:
    for name, sql in VIEWS.items():
        op.execute(f"CREATE MATERIALIZED VIEW {name} AS {sql}")
        op.execute(f"CREATE UNIQUE INDEX ux_{name} ON {name} {UNIQUE[name]}")
        op.execute(f"GRANT SELECT ON {name} TO jobscout_ro")


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
