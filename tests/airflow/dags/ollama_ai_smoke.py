"""Manual DAG for verifying Ollama-backed AI diagnosis end to end."""

from __future__ import annotations

from datetime import UTC, datetime

from airflow.sdk import dag, task


@dag(
    dag_id="dagsentry_ollama_ai_smoke",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    tags=["dagsentry", "ollama-smoke"],
)
def ollama_ai_smoke() -> None:
    @task
    def fail_with_unique_signature() -> None:
        raise ConnectionError(
            "Ollama AI smoke test: warehouse gateway refused connection on port 6543"
        )

    fail_with_unique_signature()


ollama_ai_smoke()
