"""Dagster orchestration of the JobScout data pipeline (optional ``orchestration`` extra).

The assets call the same functions as the ``jobscout`` CLI and ``make pipeline``; this
package only adds scheduling, retries, lineage, data-quality checks and the snapshot.
"""
