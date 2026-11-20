"""The Dagster ``Definitions`` for JobScout (code location ``jobscout``).

Load with ``dagster dev -m jobscout.orchestration.definitions`` (``make dagster-dev``) or
``dagster api grpc -m jobscout.orchestration.definitions`` (Compose, Kubernetes).
"""

from __future__ import annotations

import dagster as dg

from jobscout.logging import configure_logging
from jobscout.orchestration.assets.iceberg import SNAPSHOT_ASSETS
from jobscout.orchestration.assets.pipeline import PIPELINE_ASSETS, posting_expiry
from jobscout.orchestration.checks.contract import CONTRACT_CHECKS
from jobscout.orchestration.checks.privacy import PRIVACY_CHECKS
from jobscout.orchestration.checks.quality import QUALITY_CHECKS
from jobscout.orchestration.metrics import DagsterCollector, start_exporter
from jobscout.orchestration.resources import JobScoutDb, JobScoutSettings
from jobscout.orchestration.schedules import JOBS, SCHEDULES
from jobscout.orchestration.sensors import SENSORS
from jobscout.settings import get_settings

ASSETS = [*PIPELINE_ASSETS, posting_expiry, *SNAPSHOT_ASSETS]
CHECKS = [*CONTRACT_CHECKS, *QUALITY_CHECKS, *PRIVACY_CHECKS]


def build_definitions() -> dg.Definitions:
    return dg.Definitions(
        assets=ASSETS,
        asset_checks=CHECKS,
        jobs=JOBS,
        schedules=SCHEDULES,
        sensors=SENSORS,
        resources={"db": JobScoutDb(), "jobscout": JobScoutSettings()},
    )


def check_severities() -> list[tuple[dg.AssetCheckKey, str]]:
    """(check key, severity) for the metrics labels: WARN checks are the quality checks."""
    out = []
    for checks_def in CHECKS:
        for key in checks_def.check_keys:
            severity = "warn" if checks_def in QUALITY_CHECKS else "error"
            out.append((key, severity))
    return out


defs = build_definitions()

_settings = get_settings()
configure_logging(_settings.log_level, json=_settings.log_json)
if _settings.pipeline_metrics_port:
    start_exporter(
        _settings.pipeline_metrics_port,
        DagsterCollector([j.name for j in JOBS], check_severities()),
    )
