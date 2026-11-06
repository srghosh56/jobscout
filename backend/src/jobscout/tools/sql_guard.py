"""SQL guard for the text-to-SQL tool (first line of defence; the read-only role on
allow-listed views is the second, statement_timeout the third).

Accepts only:
- exactly one statement, which is a SELECT (optionally WITH ... / UNION of SELECTs),
- reading only allow-listed views (CTE names allowed), never system catalogues,
- referencing only allow-listed columns (or aliases defined in the query),
- calling only allow-listed functions,
- with no SQL comments, no SELECT INTO, no row locks,
- with a top-level LIMIT, which is clamped to ``max_rows``.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "v_postings": frozenset(
        [
            "id",
            "source",
            "url",
            "title",
            "company",
            "city",
            "cities",
            "country",
            "job_family",
            "remote_policy",
            "seniority",
            "min_years_experience",
            "required_skills",
            "nice_to_have_skills",
            "german_level",
            "english_sufficient",
            "contract_type",
            "contract_duration_months",
            "salary_min",
            "salary_max",
            "salary_currency",
            "salary_period",
            "annual_salary_min_eur",
            "annual_salary_max_eur",
            "salary_stated",
            "bluecard_status",
            "visa_sponsorship",
            "relocation_support",
            "application_deadline",
            "posting_language",
            "published_at",
            "first_seen_at",
            "last_checked_at",
            "is_active",
        ]
    ),
    "v_posting_skills": frozenset(
        ["posting_id", "skill", "kind", "city", "job_family", "seniority", "is_active"]
    ),
    "v_posting_languages": frozenset(["posting_id", "language", "level", "is_active"]),
    "mv_weekly_postings": frozenset(["week", "city", "job_family", "n_new"]),
    "mv_insights_city_family": frozenset(
        [
            "city",
            "job_family",
            "n_active",
            "n_english_friendly",
            "share_english_friendly",
            "n_salary_stated",
            "share_salary_stated",
            "n_visa_or_relocation",
            "share_visa_or_relocation",
        ]
    ),
    "mv_top_skills": frozenset(["job_family", "skill", "n", "rnk"]),
    "mv_weekly_rates": frozenset(
        ["week", "n", "share_english_friendly", "share_salary_stated", "share_visa_or_relocation"]
    ),
}
ALLOWED_TABLES = frozenset(ALLOWED_COLUMNS)
ALL_ALLOWED_COLUMNS = frozenset().union(*ALLOWED_COLUMNS.values())

ALLOWED_FUNCTIONS = frozenset(
    [
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "round",
        "lower",
        "upper",
        "coalesce",
        "nullif",
        "greatest",
        "least",
        "abs",
        "length",
        "explode",
        "unnest",
        "array_length",
        "cardinality",
        "array_agg",
        "string_agg",
        "arraycontains",
        "arrayoverlaps",
        "array_to_string",
        "arraytostring",
        "arraysize",
        "array_size",
        "arrayagg",
        "groupconcat",
        "date_trunc",
        "datetrunc",
        "timestamptrunc",
        "timestamp_trunc",
        "datetimetrunc",
        "date_trunc_",
        "extract",
        "currentdate",
        "current_date",
        "now",
        "currenttimestamp",
        "date_part",
        "datepart",
        "cast",
        "trycast",
        "percentile_cont",
        "percentilecont",
        "percentile_disc",
        "percentiledisc",
        "median",
        "rank",
        "dense_rank",
        "row_number",
        "rownumber",
        "denserank",
        "lag",
        "lead",
        "ntile",
        "first_value",
        "last_value",
        "firstvalue",
        "lastvalue",
        "filter",
        "if",
        "case",
        "trim",
        "concat",
        "initcap",
        "substring",
        "substr",
        "split_part",
        "splitpart",
        "position",
        "strposition",
        "ilike",
        "like",
        "any",
        "all",
        "in",
        "between",
        "to_char",
        "tochar",
        "stddev",
        "variance",
        "bool_and",
        "bool_or",
        "logicaland",
        "logicalor",
        "countif",
        "interval",
        "tsordstodate",
        "stddevsamp",
        "varsamp",
        "variancesamp",
        "approxquantile",
        "anyvalue",
    ]
)
DENIED_FUNCTION_HINTS = frozenset(
    [
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "set_config",
        "current_setting",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "query_to_xml",
        "copy",
        "version",
        "current_version",
        "current_user",
        "session_user",
        "current_database",
        "inet_server_addr",
    ]
)
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})


class SqlGuardError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason  # short, bounded label for metrics


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    limit: int


def _func_name(f: exp.Func) -> str:
    if isinstance(f, exp.Anonymous):
        return str(f.name).lower()
    return f.sql_name().lower()


def _top_limit(tree: exp.Expression) -> int | None:
    limit = tree.args.get("limit")
    if limit is None:
        return None
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    raise SqlGuardError("bad_limit", "LIMIT must be a plain integer literal")


def check_sql(sql: str, max_rows: int = 50) -> GuardedSql:
    raw = (sql or "").strip()
    if not raw:
        raise SqlGuardError("empty", "empty query")
    if len(raw) > 4000:
        raise SqlGuardError("too_long", "query longer than 4000 characters")
    raw = raw.rstrip(";").strip()
    try:
        tokens = sqlglot.tokenize(raw, read="postgres")
    except (TokenError, ParseError) as exc:
        raise SqlGuardError("parse_error", f"could not tokenize SQL: {exc}") from exc
    if any(t.comments for t in tokens) or "--" in raw or "/*" in raw:
        raise SqlGuardError("comment", "SQL comments are not allowed")
    if any(t.token_type == sqlglot.TokenType.SEMICOLON for t in tokens):
        raise SqlGuardError("multiple_statements", "exactly one statement is allowed")
    try:
        statements = [s for s in sqlglot.parse(raw, read="postgres") if s is not None]
    except (ParseError, TokenError) as exc:
        raise SqlGuardError("parse_error", f"could not parse SQL: {str(exc)[:200]}") from exc
    if len(statements) != 1:
        raise SqlGuardError("multiple_statements", "exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise SqlGuardError("not_select", f"only SELECT is allowed, got {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(
            node,
            exp.Insert
            | exp.Update
            | exp.Delete
            | exp.Create
            | exp.Drop
            | exp.Alter
            | exp.Command
            | exp.Merge
            | exp.TruncateTable
            | exp.Copy
            | exp.Grant
            | exp.Set
            | exp.Into
            | exp.Lock,
        ):
            raise SqlGuardError("ddl_dml", f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise SqlGuardError("ddl_dml", "row locks (FOR UPDATE/SHARE) are not allowed")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        schema = (t.db or "").lower()
        if schema in SYSTEM_SCHEMAS or name.startswith("pg_"):
            raise SqlGuardError(
                "system_catalog",
                f"system catalogue {schema + '.' if schema else ''}{name} is not allowed",
            )
        if schema and schema != "public":
            raise SqlGuardError("forbidden_table", f"schema {schema} is not allowed")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SqlGuardError(
                "forbidden_table",
                f"table {name} is not allowed; use one of: {', '.join(sorted(ALLOWED_TABLES))}",
            )

    for f in tree.find_all(exp.Func):
        if isinstance(f, exp.Connector):  # AND / OR / XOR are operators, not functions
            continue
        name = _func_name(f)
        if name in DENIED_FUNCTION_HINTS or name.startswith("pg_"):
            raise SqlGuardError("dangerous_function", f"function {name} is not allowed")
        if name not in ALLOWED_FUNCTIONS:
            raise SqlGuardError("function_not_allowed", f"function {name} is not in the allow-list")

    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    aliases |= {t.alias.lower() for t in tree.find_all(exp.TableAlias) if t.name}
    for ta in tree.find_all(exp.TableAlias):
        aliases |= {c.name.lower() for c in ta.columns}
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if not name or isinstance(col.this, exp.Star):
            continue
        if name not in ALL_ALLOWED_COLUMNS and name not in aliases:
            raise SqlGuardError("column_not_allowed", f"column {name} is not allowed")

    limit = _top_limit(tree)
    if limit is None:
        raise SqlGuardError(
            "missing_limit", f"add a LIMIT (at most {max_rows}) to the outermost query"
        )
    if limit > max_rows:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        limit = max_rows
    return GuardedSql(sql=tree.sql(dialect="postgres"), limit=limit)


SCHEMA_DOC = """Read-only views (PostgreSQL):
- v_postings(id, source, url, title, company, city, cities text[], country, job_family, remote_policy ['onsite','hybrid','remote','unknown'], seniority ['intern','junior','mid','senior','staff_plus','unknown'], min_years_experience int, required_skills text[], nice_to_have_skills text[], german_level ['none','unspecified','basic','good','fluent','native'], english_sufficient ['yes','no','unclear'], contract_type, contract_duration_months, salary_min, salary_max, salary_currency, salary_period ['year','month','hour'], annual_salary_min_eur, annual_salary_max_eur, salary_stated bool, bluecard_status ['meets_standard','meets_reduced_only','below','salary_not_stated'], visa_sponsorship ['yes','no','not_mentioned'], relocation_support ['yes','no','not_mentioned'], application_deadline date, posting_language ['en','de'], published_at date, first_seen_at, last_checked_at, is_active bool)
- v_posting_skills(posting_id, skill lowercase, kind ['required','nice_to_have'], city, job_family, seniority, is_active)
- v_posting_languages(posting_id, language lowercase, level, is_active)
- mv_insights_city_family(city, job_family, n_active, n_english_friendly, share_english_friendly, n_salary_stated, share_salary_stated, n_visa_or_relocation, share_visa_or_relocation)
- mv_top_skills(job_family, skill, n, rnk)
- mv_weekly_postings(week, city, job_family, n_new)
- mv_weekly_rates(week, n, share_english_friendly, share_salary_stated, share_visa_or_relocation)
job_family values: data_ml, software_engineering, devops_cloud, security, product_design, engineering_hw, sales_marketing, other.
City names are German spellings: 'München', 'Köln', 'Frankfurt am Main', 'Nürnberg', 'Düsseldorf'.
salary_stated is a boolean: filter with salary_stated, never IS NOT NULL. required_skills and nice_to_have_skills are lists: use v_posting_skills for skills. For Blue Card results use bluecard_status ('meets_standard', 'meets_reduced_only', 'below', 'salary_not_stated'), never a salary threshold number.
Rules: exactly one SELECT; always filter is_active = true unless asked about past postings; always end with LIMIT <= 50; no comments."""
