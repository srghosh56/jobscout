"""Unit tests for the orchestration rules: masking, check rules, schedules, sensor, metrics."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

dg = pytest.importorskip("dagster")

from jobscout.orchestration.checks import contract, privacy, quality  # noqa: E402
from jobscout.orchestration.masking import find_contacts, mask_contacts  # noqa: E402

# ---------- masking ----------


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Mail hr.team+jobs@firma-x.de today", "Mail [email] today"),
        ("Tel. +49 30 23125001.", "Tel. [phone]."),
        ("Ruf an: 030/1234567", "Ruf an: [phone]"),
        ("(030) 123 456-78", "[phone]"),
        ("0171-1234567 mobil", "[phone] mobil"),
        ("+49 (0)30 12345678", "[phone]"),
        ("Telefon 40 2312500", "Telefon [phone]"),
    ],
)
def test_mask_contacts_masks_emails_and_phones(text: str, masked: str) -> None:
    assert mask_contacts(text) == masked
    assert find_contacts(text)
    assert find_contacts(masked) == []


@pytest.mark.parametrize(
    "text",
    [
        "Salary 70,000 - 85,000 EUR per year",
        "Gehalt 65.000 bis 80.000 Euro",
        "Start 01.10.2026 or 01/10/2026, since 2019, 2026-11-20",
        "PLZ 10115 Berlin, 5 Jahre, 30 Tage",
        "Referenz 10000-1000000001-S",
    ],
)
def test_mask_contacts_keeps_numbers_that_are_not_contacts(text: str) -> None:
    assert mask_contacts(text) == text
    assert find_contacts(text) == []


# ---------- contract rules ----------


def _extracted(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "an-0123456789",
        "title": "Data Engineer",
        "company": "Fictional GmbH",
        "locations": [{"city": "Berlin", "country": "Germany"}],
        "remote_policy": "hybrid",
        "seniority": "mid",
        "min_years_experience": 3,
        "required_skills": ["Python"],
        "nice_to_have_skills": [],
        "languages_required": [{"language": "English", "level": "fluent"}],
        "english_sufficient": "yes",
        "contract_type": "permanent",
        "contract_duration_months": None,
        "salary_min": 60000.0,
        "salary_max": 70000.0,
        "salary_currency": "EUR",
        "salary_period": "year",
        "visa_sponsorship": "not_mentioned",
        "relocation_support": "not_mentioned",
        "application_deadline": None,
        "posting_language": "en",
    }
    return {**row, **kw}


def test_ids_rules() -> None:
    assert contract.duplicate_ids(["ba-0000000001", "ba-0000000001", "an-0000000002"]) == [
        "ba-0000000001"
    ]
    assert contract.malformed_ids(["ba-0000000001", "BAD ID", "xx-0000000001"]) == [
        "BAD ID",
        "xx-0000000001",
    ]
    assert contract.missing_required([{"id": "a", "title": ""}]) == ["a"]


def test_invalid_extractions_uses_the_pydantic_schema() -> None:
    assert contract.invalid_extractions([_extracted()]) == []
    bad = contract.invalid_extractions(
        [_extracted(seniority="wizard"), _extracted(id="an-1", salary_min=9.0, salary_max=1.0)]
    )
    assert [b[0] for b in bad] == ["an-0123456789", "an-1"]
    assert bad[0][1] == "seniority"


def test_salary_rules() -> None:
    ok = {
        "id": "a",
        "salary_stated": True,
        "salary_min": 300.0,
        "salary_max": None,
        "salary_currency": "EUR",
        "salary_period": "month",
        "annual_salary_min_eur": 3600.0,
        "annual_salary_max_eur": None,
    }
    assert contract.salary_problems([ok, {"id": "b", "salary_stated": False}]) == []
    cases = {
        "min > max": {"salary_min": 9.0, "salary_max": 1.0},
        "currency USD": {"salary_currency": "USD"},
        "period week": {"salary_period": "week"},
        "salary_stated without an amount": {"salary_min": None},
        "annual_salary_min_eur=50 outside plausible range": {"annual_salary_min_eur": 50.0},
    }
    for reason, change in cases.items():
        assert contract.salary_problems([{**ok, **change}]) == [("a", reason)]
    assert contract.salary_problems([{**ok, "salary_stated": False}]) == [
        ("a", "amount without salary_stated")
    ]


# ---------- quality rules ----------


def test_freshness_and_volume_rules() -> None:
    now = dt.datetime(2026, 11, 20, tzinfo=dt.UTC)
    assert quality.is_fresh(now - dt.timedelta(days=7), now)
    assert not quality.is_fresh(now - dt.timedelta(days=9), now)
    assert not quality.is_fresh(None, now)
    runs = [
        {"source": "arbeitnow", "fetched": 400},
        {"source": "arbeitnow", "fetched": 1000},
        {"source": "arbeitsagentur", "fetched": 900},
        {"source": "arbeitsagentur", "fetched": 1000},
        {"source": "new", "fetched": 5},
    ]
    assert quality.volume_drops(runs) == {"arbeitnow": (400, 1000)}


# ---------- privacy rules ----------


def test_cv_text_columns_and_profiles() -> None:
    cols = [
        {"table_name": "postings", "column_name": "raw_text"},
        {"table_name": "cv_profiles", "column_name": "profile"},
        {"table_name": "feedback", "column_name": "cv_text"},
        {"table_name": "uploads", "column_name": "resume"},
    ]
    assert privacy.cv_text_columns(cols) == ["feedback.cv_text", "uploads.resume"]
    good = {"id": 1, "profile": {"headline": "Data engineer", "skills": ["Python"]}}
    assert privacy.profile_problems([good]) == []
    leaky = {"id": 2, "profile": '{"headline": "call 0171-1234567", "cv_text": "..."}'}
    assert privacy.profile_problems([leaky]) == [
        "2: unexpected keys ['cv_text']",
        "2: contact data",
    ]


# ---------- schedules ----------


def test_weekly_schedule_requests_one_run_over_both_sources() -> None:
    from jobscout.orchestration.schedules import RANGE_END, RANGE_START, weekly_pipeline_schedule

    when = dt.datetime(2026, 11, 23, 3, 0, tzinfo=dt.UTC)
    request = weekly_pipeline_schedule(dg.build_schedule_context(scheduled_execution_time=when))
    assert request.tags["jobscout/week"] == "2026-W48"
    assert request.run_key == "weekly-" + request.tags["jobscout/week"]  # one run per week
    assert request.tags[RANGE_START] == "arbeitsagentur"
    assert request.tags[RANGE_END] == "arbeitnow"


def test_schedules_and_definitions_load() -> None:
    from jobscout.orchestration.definitions import defs
    from jobscout.orchestration.schedules import daily_expiry_schedule, weekly_pipeline_schedule

    assert weekly_pipeline_schedule.cron_schedule == "0 3 * * 1"
    assert daily_expiry_schedule.cron_schedule == "30 4 * * *"
    assert weekly_pipeline_schedule.execution_timezone == "Europe/Berlin"
    job = defs.resolve_job_def("weekly_pipeline")
    assert job.partitions_def is not None
    assert set(job.partitions_def.get_partition_keys()) == {"arbeitsagentur", "arbeitnow"}
    dg.Definitions.validate_loadable(defs)


def test_settings_resource_rejects_unknown_mode() -> None:
    from jobscout.orchestration.resources import JobScoutSettings

    assert JobScoutSettings(ingest_mode="fixtures").mode == "fixtures"
    with pytest.raises(ValueError, match="ingest_mode"):
        _ = JobScoutSettings(ingest_mode="nope").mode


# ---------- sensor and metrics ----------


@dg.op
def _boom() -> None:
    raise RuntimeError("source API down")


@dg.job
def _failing_job() -> None:
    _boom()


def test_run_failure_sensor_logs_and_counts(capsys: pytest.CaptureFixture[str]) -> None:
    from jobscout.metrics import PIPELINE_RUN_FAILURES
    from jobscout.orchestration.sensors import pipeline_run_failure_sensor

    instance = dg.DagsterInstance.ephemeral()
    result = _failing_job.execute_in_process(instance=instance, raise_on_error=False)
    before = PIPELINE_RUN_FAILURES.labels(job="_failing_job")._value.get()
    context = dg.build_run_status_sensor_context(
        sensor_name="pipeline_run_failure_sensor",
        dagster_instance=instance,
        dagster_run=result.dagster_run,
        dagster_event=result.get_run_failure_event(),
    ).for_run_failure()
    pipeline_run_failure_sensor(context)
    assert PIPELINE_RUN_FAILURES.labels(job="_failing_job")._value.get() == before + 1
    err = capsys.readouterr().err
    assert '"event": "pipeline_run_failed"' in err and '"_boom"' in err


def test_metrics_collector_reports_runs_and_checks() -> None:
    from jobscout.orchestration.metrics import DagsterCollector

    instance = dg.DagsterInstance.ephemeral()
    _failing_job.execute_in_process(instance=instance, raise_on_error=False)
    key = dg.AssetCheckKey(dg.AssetKey("raw_postings"), "raw_postings_fresh")
    families = {
        f.name: f.samples
        for f in DagsterCollector(["_failing_job"], [(key, "warn")], instance).collect()
    }
    failures = [s for s in families["jobscout_pipeline_runs"] if s.labels["status"] == "failure"]
    assert failures[0].value == 1
    assert families["jobscout_pipeline_check_passed"] == []  # no check has run yet


def test_exporter_starts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from prometheus_client import REGISTRY

    from jobscout.orchestration import metrics

    monkeypatch.setattr(metrics, "_started", False)
    monkeypatch.setattr(metrics, "start_http_server", lambda port: None)
    collector = metrics.DagsterCollector([], [], dg.DagsterInstance.ephemeral())
    try:
        assert metrics.start_exporter(9999, collector) is True
        assert metrics.start_exporter(9999, collector) is False
    finally:
        REGISTRY.unregister(collector)

    def taken(port: int) -> None:
        raise OSError("in use")

    monkeypatch.setattr(metrics, "_started", False)
    monkeypatch.setattr(metrics, "start_http_server", taken)
    assert metrics.start_exporter(9999, collector) is False
