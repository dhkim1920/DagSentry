from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from dagsentry.airflow.collector import CollectorSettings
from dagsentry.airflow.reconciler import (
    AirflowTaskHistoryClient,
    FailureReconciler,
    ReconcilerSettings,
    TaskInstanceReference,
    TaskTryHistory,
)
from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import (
    CollectionSource,
    FailureEventCreate,
    FailureState,
)
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def settings() -> ReconcilerSettings:
    return ReconcilerSettings(
        collector=CollectorSettings(
            environment="production",
            ingest_url="https://dagsentry.test/api/v1/failure-events",
            ingest_api_token="ingest-secret",
        ),
        airflow_api_base_url="https://airflow.test",
        airflow_api_token="airflow-secret",
        request_timeout_seconds=1,
        max_attempts=2,
        retry_backoff_seconds=0.1,
        page_size=2,
        overlap=timedelta(minutes=5),
        initial_lookback=timedelta(hours=24),
    )


@dataclass
class MemoryWatermark:
    value: datetime | None = None
    saves: list[datetime] = field(default_factory=list)

    def load(self) -> datetime | None:
        return self.value

    def save(self, value: datetime) -> None:
        self.value = value
        self.saves.append(value)


@dataclass
class RecordingSender:
    events: list[FailureEventCreate] = field(default_factory=list)
    fail_on_call: int | None = None
    calls: int = 0

    def send(self, event: FailureEventCreate) -> None:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise httpx.ConnectError("ingest unavailable")
        self.events.append(event)


@dataclass
class StubAirflowClient:
    references: list[TaskInstanceReference]
    histories: dict[str, list[TaskTryHistory]]
    windows: list[tuple[datetime, datetime]] = field(default_factory=list)

    def iter_task_instances(
        self,
        *,
        updated_at_gte: datetime,
        updated_at_lt: datetime,
    ) -> list[TaskInstanceReference]:
        self.windows.append((updated_at_gte, updated_at_lt))
        return self.references

    def task_tries(self, reference: TaskInstanceReference) -> list[TaskTryHistory]:
        return self.histories[reference.task_id]


def reference(task_id: str = "load", *, map_index: int = -1) -> TaskInstanceReference:
    return TaskInstanceReference(
        dag_id="orders",
        dag_run_id="scheduled__2026-08-11",
        task_id=task_id,
        map_index=map_index,
    )


def history(
    *,
    task_id: str = "load",
    map_index: int = -1,
    try_number: int,
    state: str,
) -> TaskTryHistory:
    return TaskTryHistory(
        dag_id="orders",
        dag_run_id="scheduled__2026-08-11",
        task_id=task_id,
        map_index=map_index,
        try_number=try_number,
        state=state,
        end_date=NOW - timedelta(minutes=try_number),
        operator="PythonOperator",
    )


def test_airflow_client_retries_and_follows_cursor_pages() -> None:
    requests: list[httpx.Request] = []
    attempts = 0
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        requests.append(request)
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary", request=request)
        cursor = request.url.params.get("cursor")
        if cursor == "":
            return httpx.Response(
                200,
                json={"task_instances": [reference().model_dump()], "next_cursor": "page-2"},
            )
        return httpx.Response(
            200,
            json={
                "task_instances": [reference("mapped", map_index=3).model_dump()],
                "next_cursor": None,
            },
        )

    client = AirflowTaskHistoryClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        sleep=sleeps.append,
    )

    results = list(
        client.iter_task_instances(
            updated_at_gte=NOW - timedelta(minutes=10),
            updated_at_lt=NOW,
        )
    )

    assert [item.task_id for item in results] == ["load", "mapped"]
    assert sleeps == [0.1]
    assert requests[-1].url.params["cursor"] == "page-2"
    assert requests[-1].url.params["limit"] == "2"
    assert requests[-1].headers["Authorization"] == "Bearer airflow-secret"
    assert requests[-1].url.params["updated_at_lt"] == NOW.isoformat()


def test_reconciler_maps_failed_history_and_advances_overlap_watermark() -> None:
    airflow = StubAirflowClient(
        references=[reference(), reference("mapped", map_index=3)],
        histories={
            "load": [
                history(try_number=1, state="up_for_retry"),
                history(try_number=2, state="success"),
            ],
            "mapped": [history(task_id="mapped", map_index=3, try_number=1, state="failed")],
        },
    )
    sender = RecordingSender()
    watermark = MemoryWatermark(NOW - timedelta(minutes=10))
    reconciler = FailureReconciler(
        settings(),
        airflow_client=airflow,
        event_sender=sender,
        watermark_store=watermark,
        clock=lambda: NOW,
    )

    result = reconciler.run()

    assert airflow.windows == [(NOW - timedelta(minutes=15), NOW)]
    assert result.task_instances_scanned == 2
    assert result.task_tries_scanned == 3
    assert result.failure_events_sent == 2
    assert watermark.value == NOW
    assert [event.state for event in sender.events] == [
        FailureState.UP_FOR_RETRY,
        FailureState.FAILED,
    ]
    assert all(event.source == CollectionSource.RECONCILER for event in sender.events)
    assert sender.events[1].map_index == 3


def test_partial_ingest_failure_does_not_advance_watermark_and_restart_replays() -> None:
    airflow = StubAirflowClient(
        references=[reference()],
        histories={
            "load": [
                history(try_number=1, state="up_for_retry"),
                history(try_number=2, state="failed"),
            ]
        },
    )
    sender = RecordingSender(fail_on_call=2)
    watermark = MemoryWatermark()
    reconciler = FailureReconciler(
        settings(),
        airflow_client=airflow,
        event_sender=sender,
        watermark_store=watermark,
        clock=lambda: NOW,
    )

    with pytest.raises(httpx.ConnectError):
        reconciler.run()

    assert watermark.value is None
    sender.fail_on_call = None
    result = reconciler.run()

    assert result.failure_events_sent == 2
    assert watermark.value == NOW
    assert sender.calls == 4


def test_reconciler_restores_missed_event_and_ingest_deduplicates_overlap(
    session_factory: SessionFactory,
) -> None:
    first = history(try_number=1, state="up_for_retry")
    missed = history(try_number=2, state="failed")
    assert first.end_date is not None
    with session_factory() as session:
        ingest_failure_event(
            session,
            FailureEventCreate(
                environment="production",
                dag_id=first.dag_id,
                dag_run_id=first.dag_run_id,
                task_id=first.task_id,
                map_index=first.map_index,
                try_number=first.try_number,
                source=CollectionSource.RETRY_CALLBACK,
                state=FailureState.UP_FOR_RETRY,
                observed_at=first.end_date,
                operator_type=first.operator,
            ),
        )

    @dataclass
    class DatabaseSender:
        created: list[bool] = field(default_factory=list)

        def send(self, event: FailureEventCreate) -> None:
            with session_factory() as session:
                self.created.append(ingest_failure_event(session, event).created)

    airflow = StubAirflowClient(
        references=[reference()],
        histories={"load": [first, missed]},
    )
    sender = DatabaseSender()
    watermark = MemoryWatermark()
    reconciler = FailureReconciler(
        settings(),
        airflow_client=airflow,
        event_sender=sender,
        watermark_store=watermark,
        clock=lambda: NOW,
    )

    reconciler.run()
    reconciler.run()

    assert sender.created == [False, True, False, False]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 2
        assert session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord)) == 2
        restored = session.scalar(
            select(FailureEventRecord).where(FailureEventRecord.try_number == 2)
        )
        assert restored is not None
        assert restored.source == CollectionSource.RECONCILER
