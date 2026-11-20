"""Prometheus exporter for the Dagster pipeline, served by the Dagster code server.

The code server is the long-running process where sensors are evaluated, so the
run-failure counter lives there. A custom collector adds gauges read from the Dagster
instance (run and check storage) at scrape time:

- ``jobscout_pipeline_runs{job, status}``: runs in Dagster's run storage
- ``jobscout_pipeline_last_success_timestamp_seconds{job}``
- ``jobscout_pipeline_check_passed{check, asset, severity}``: latest result, 1 or 0

Labels come from the definitions (job, asset and check names), so cardinality is fixed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import dagster as dg
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from jobscout.logging import get_logger

log = get_logger(__name__)
REPORTED_STATUSES = (
    dg.DagsterRunStatus.SUCCESS,
    dg.DagsterRunStatus.FAILURE,
    dg.DagsterRunStatus.STARTED,
    dg.DagsterRunStatus.QUEUED,
    dg.DagsterRunStatus.CANCELED,
)
_started = False


class DagsterCollector(Collector):
    def __init__(
        self,
        job_names: Sequence[str],
        checks: Sequence[tuple[dg.AssetCheckKey, str]],
        instance: dg.DagsterInstance | None = None,
    ) -> None:
        self.job_names = list(job_names)
        self.checks = list(checks)  # (key, severity)
        self._instance = instance

    def instance(self) -> dg.DagsterInstance:
        if self._instance is None:
            self._instance = dg.DagsterInstance.get()
        return self._instance

    def collect(self) -> Iterator[GaugeMetricFamily]:
        runs = GaugeMetricFamily(
            "jobscout_pipeline_runs", "Dagster runs in run storage", labels=["job", "status"]
        )
        last_ok = GaugeMetricFamily(
            "jobscout_pipeline_last_success_timestamp_seconds",
            "End time of the last successful run",
            labels=["job"],
        )
        checks = GaugeMetricFamily(
            "jobscout_pipeline_check_passed",
            "Latest asset check result (1 passed, 0 failed)",
            labels=["check", "asset", "severity"],
        )
        try:
            inst = self.instance()
            for job in self.job_names:
                for status in REPORTED_STATUSES:
                    n = inst.get_runs_count(dg.RunsFilter(job_name=job, statuses=[status]))
                    runs.add_metric([job, status.value.lower()], n)
                ok = inst.get_runs(
                    dg.RunsFilter(job_name=job, statuses=[dg.DagsterRunStatus.SUCCESS]), limit=1
                )
                if ok:
                    record = inst.get_run_record_by_id(ok[0].run_id)
                    if record is not None and record.end_time is not None:
                        last_ok.add_metric([job], record.end_time)
            for key, severity in self.checks:
                rec = inst.get_latest_asset_check_evaluation_record(key)
                if rec is None or rec.status.value not in ("SUCCEEDED", "FAILED"):
                    continue
                passed = 1.0 if rec.status.value == "SUCCEEDED" else 0.0
                checks.add_metric([key.name, key.asset_key.to_user_string(), severity], passed)
        except Exception as exc:  # a scrape must never crash the code server
            log.warning("pipeline_metrics_failed", error=type(exc).__name__)
        yield runs
        yield last_ok
        yield checks


def start_exporter(port: int, collector: DagsterCollector) -> bool:
    """Serve /metrics once per process. Run workers started from the code server inherit
    its environment; they find the port taken and skip the exporter."""
    global _started
    if _started:
        return False
    try:
        start_http_server(port)
    except OSError:
        log.debug("pipeline_metrics_port_in_use", port=port)
        return False
    REGISTRY.register(collector)
    _started = True
    log.info("pipeline_metrics_started", port=port)
    return True
