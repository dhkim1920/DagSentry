from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import ingest_failure_event
from dagsentry.metrics import increment_counter, render_prometheus_metrics
from dagsentry.models import DiagnosisOutboxRecord, OperationalMetricCounterRecord

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def counter_value(
    session_factory: SessionFactory,
    metric_name: str,
    label_value: str,
) -> int:
    with session_factory() as session:
        record = session.get(
            OperationalMetricCounterRecord,
            (metric_name, label_value),
        )
        return record.value if record is not None else 0


def test_counter_allowlist_rejects_high_cardinality_labels(
    session_factory: SessionFactory,
) -> None:
    with session_factory.begin() as session:
        increment_counter(session, "dagsentry_worker_jobs_total", "retry", amount=2, now=NOW)

    assert counter_value(session_factory, "dagsentry_worker_jobs_total", "retry") == 2
    with session_factory() as session:
        with pytest.raises(ValueError, match="Invalid label value"):
            increment_counter(
                session,
                "dagsentry_worker_jobs_total",
                "customer_dag_id",
            )
        with pytest.raises(ValueError, match="Unknown operational counter"):
            increment_counter(session, "custom_metric", "anything")


def test_prometheus_output_contains_durable_counters_and_database_gauges(
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        result = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="test",
                dag_id="orders",
                dag_run_id="run",
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=NOW,
            ),
        )
        with session.begin():
            outbox = session.scalar(
                select(DiagnosisOutboxRecord).where(
                    DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
                )
            )
            assert outbox is not None
            outbox.available_at = NOW - timedelta(seconds=45)
            increment_counter(
                session,
                "dagsentry_recovery_checker_runs_total",
                "success",
                now=NOW - timedelta(seconds=30),
            )

    metrics = render_prometheus_metrics(session_factory, now=NOW)

    assert 'dagsentry_outbox_jobs{status="PENDING"} 1' in metrics
    assert "dagsentry_outbox_oldest_ready_age_seconds 45.0" in metrics
    assert 'dagsentry_recovery_checker_runs_total{result="success"} 1' in metrics
    assert "dagsentry_recovery_checker_delay_seconds 30.0" in metrics
    assert "orders" not in metrics


def test_recovery_checker_delay_is_nan_before_first_run(
    session_factory: SessionFactory,
) -> None:
    metrics = render_prometheus_metrics(session_factory, now=NOW)

    assert "dagsentry_recovery_checker_delay_seconds NaN" in metrics


def test_metrics_endpoint_counts_created_duplicate_and_failed_ingest(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    payload = {
        "environment": "test",
        "dag_id": "orders",
        "dag_run_id": "run",
        "task_id": "load",
        "map_index": -1,
        "try_number": 1,
        "source": "LISTENER",
        "state": "FAILED",
        "observed_at": NOW.isoformat(),
    }

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-DagSentry-Token": "test-ingest-token"}
            assert (
                await client.post("/api/v1/failure-events", json=payload, headers=headers)
            ).json()["created"] is True
            assert (
                await client.post("/api/v1/failure-events", json=payload, headers=headers)
            ).json()["created"] is False
            failed = await client.post("/api/v1/failure-events", json=payload)
            assert failed.status_code == 401
            return await client.get("/metrics")

    response = asyncio.run(exercise())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'dagsentry_ingest_events_total{result="created"} 1' in response.text
    assert 'dagsentry_ingest_events_total{result="duplicate"} 1' in response.text
    assert 'dagsentry_ingest_events_total{result="failure"} 1' in response.text
