"""Airflow DAG that repairs missed DagSentry Failure Events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task

from dagsentry.airflow.reconciler import run_reconciliation_from_airflow


@dag(
    dag_id="dagsentry_failure_reconciler",
    schedule="*/5 * * * *",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["dagsentry", "reliability"],
)
def reconciliation_dag() -> None:
    @task(retries=2, retry_delay=timedelta(seconds=30))
    def reconcile() -> None:
        run_reconciliation_from_airflow()

    reconcile()


reconciliation_dag()
