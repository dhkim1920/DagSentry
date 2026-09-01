"""Manual Airflow 3 DAG used by the v0.1 collection smoke workflow."""

from __future__ import annotations

from datetime import UTC, datetime

from airflow.sdk import dag, get_current_context, task


@dag(
    dag_id="dagsentry_v01_failure_scenarios",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    tags=["dagsentry", "v0.1-smoke"],
)
def failure_scenarios() -> None:
    @task(retries=1)
    def retry_then_success() -> None:
        if get_current_context()["ti"].try_number == 1:
            raise RuntimeError("intentional first Try failure")

    @task(retries=2)
    def always_fails() -> None:
        raise RuntimeError("intentional terminal failure")

    @task
    def mapped_failure(value: int) -> None:
        raise RuntimeError(f"intentional mapped failure {value}")

    retry_then_success()
    always_fails()
    mapped_failure.expand(value=[0, 1])


failure_scenarios()
