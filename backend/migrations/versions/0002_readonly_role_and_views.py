"""read-only role and allow-listed views for the text-to-SQL tool

The ``jobscout_ro`` role can SELECT from these views only: no base tables, no
raw posting text, no feedback/API keys/CV data. Its password and LOGIN are set
outside migrations (``jobscout migrate`` reads READONLY_DATABASE_URL), so no secret
is ever stored in a migration file.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RO_ROLE = "jobscout_ro"

V_POSTINGS = """
CREATE VIEW v_postings AS
SELECT id, source, url, title, company, city, cities, country, job_family,
       remote_policy, seniority, min_years_experience, required_skills,
       nice_to_have_skills, german_level, english_sufficient, contract_type,
       contract_duration_months, salary_min, salary_max, salary_currency, salary_period,
       annual_salary_min_eur, annual_salary_max_eur, salary_stated, bluecard_status,
       visa_sponsorship, relocation_support, application_deadline, posting_language,
       published_at, first_seen_at, last_checked_at, is_active
FROM postings
WHERE duplicate_of IS NULL AND extraction_status = 'ok'
"""

V_POSTING_SKILLS = """
CREATE VIEW v_posting_skills AS
SELECT p.id AS posting_id, lower(s.skill) AS skill, s.kind, p.city, p.job_family,
       p.seniority, p.is_active
FROM postings p
CROSS JOIN LATERAL (
    SELECT unnest(p.required_skills) AS skill, 'required'::text AS kind
    UNION ALL
    SELECT unnest(p.nice_to_have_skills), 'nice_to_have'::text
) s
WHERE p.duplicate_of IS NULL AND p.extraction_status = 'ok'
"""

V_POSTING_LANGUAGES = """
CREATE VIEW v_posting_languages AS
SELECT p.id AS posting_id, lower(l->>'language') AS language, l->>'level' AS level,
       p.is_active
FROM postings p
CROSS JOIN LATERAL jsonb_array_elements(p.languages_required) l
WHERE p.duplicate_of IS NULL AND p.extraction_status = 'ok'
"""

VIEWS = ("v_postings", "v_posting_skills", "v_posting_languages")


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RO_ROLE}') THEN
            CREATE ROLE {RO_ROLE} NOLOGIN;
          END IF;
        END $$;
        """
    )
    op.execute(V_POSTINGS)
    op.execute(V_POSTING_SKILLS)
    op.execute(V_POSTING_LANGUAGES)
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {RO_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RO_ROLE}")
    for v in VIEWS:
        op.execute(f"GRANT SELECT ON {v} TO {RO_ROLE}")
    # Defence in depth: even if a write slipped through, the role can't write.
    op.execute(f"ALTER ROLE {RO_ROLE} SET default_transaction_read_only = on")
    op.execute(f"ALTER ROLE {RO_ROLE} SET statement_timeout = '3s'")


def downgrade() -> None:
    for v in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {v}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {RO_ROLE}")
