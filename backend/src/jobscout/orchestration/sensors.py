"""Run-failure sensor: one structured log event and one Prometheus counter increment per
failed run. Prometheus alerting (deploy/prometheus/alerts.yml) turns it into an alert."""

import dagster as dg

from jobscout.logging import get_logger
from jobscout.metrics import PIPELINE_RUN_FAILURES

log = get_logger(__name__)


def failed_steps(context: dg.RunFailureSensorContext) -> list[str]:
    return sorted({e.step_key for e in context.get_step_failure_events() if e.step_key})


@dg.run_failure_sensor(
    name="pipeline_run_failure_sensor",
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Logs `pipeline_run_failed` and increments jobscout_pipeline_run_failures_total.",
)
def pipeline_run_failure_sensor(context: dg.RunFailureSensorContext) -> None:
    job = context.dagster_run.job_name
    PIPELINE_RUN_FAILURES.labels(job=job).inc()
    error = context.failure_event.message or ""
    log.error(
        "pipeline_run_failed",
        job=job,
        run_id=context.dagster_run.run_id,
        failed_steps=failed_steps(context),
        error=error[:300],
    )


SENSORS = [pipeline_run_failure_sensor]
