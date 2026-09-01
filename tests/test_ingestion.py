import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord, OutboxStatus


class LargeBodyWithoutContentLength(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"x" * 1_000

    async def aclose(self) -> None:
        return None


def event() -> FailureEventCreate:
    return FailureEventCreate(
        environment="production",
        dag_id="daily_orders",
        dag_run_id="scheduled__2026-08-09T00:00:00+00:00",
        task_id="load_orders",
        map_index=-1,
        try_number=1,
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
        observed_at=datetime(2026, 8, 9, tzinfo=UTC),
        operator_type="PythonOperator",
    )


def event_payload() -> dict[str, object]:
    return {
        "environment": "production",
        "dag_id": "daily_orders",
        "dag_run_id": "scheduled__2026-08-09T00:00:00+00:00",
        "task_id": "load_orders",
        "map_index": -1,
        "try_number": 1,
        "source": "LISTENER",
        "state": "FAILED",
        "observed_at": "2026-08-09T00:00:00Z",
        "operator_type": "PythonOperator",
    }


def test_ingestion_atomically_creates_failure_and_outbox(session: Session) -> None:
    result = ingest_failure_event(session, event())

    stored_event = session.get(FailureEventRecord, result.failure_event_id)
    outbox = session.scalar(
        select(DiagnosisOutboxRecord).where(
            DiagnosisOutboxRecord.failure_event_id == result.failure_event_id
        )
    )

    assert result.created is True
    assert stored_event is not None
    assert stored_event.event_key == result.event_key
    assert outbox is not None
    assert outbox.status == OutboxStatus.PENDING
    assert outbox.attempt_count == 0


def test_duplicate_ingestion_returns_existing_event(session: Session) -> None:
    first = ingest_failure_event(session, event())
    duplicate = ingest_failure_event(session, event())

    assert duplicate.created is False
    assert duplicate.failure_event_id == first.failure_event_id
    assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord)) == 1


def test_outbox_failure_rolls_back_failure_event(session: Session) -> None:
    def reject_outbox(*_: object) -> None:
        raise RuntimeError("simulated outbox failure")

    sqlalchemy_event.listen(DiagnosisOutboxRecord, "before_insert", reject_outbox)
    try:
        with pytest.raises(RuntimeError, match="simulated outbox failure"):
            ingest_failure_event(session, event())
    finally:
        sqlalchemy_event.remove(DiagnosisOutboxRecord, "before_insert", reject_outbox)

    assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 0
    assert session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord)) == 0


def test_ingest_api_requires_valid_token(
    settings: Settings, session_factory: SessionFactory
) -> None:
    async def request(token: str | None) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        headers = {"X-DagSentry-Token": token} if token else {}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events", json=event_payload(), headers=headers
            )

    missing = asyncio.run(request(None))
    invalid = asyncio.run(request("wrong-token"))

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_ingest_api_is_unavailable_without_configured_token(
    settings: Settings, session_factory: SessionFactory
) -> None:
    settings.ingest_api_token = None

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/v1/failure-events", json=event_payload())

    response = asyncio.run(request())

    assert response.status_code == 503
    assert response.json() == {"detail": "Failure ingestion is not configured"}


def test_ingest_api_is_idempotent(settings: Settings, session_factory: SessionFactory) -> None:
    async def request_twice() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        headers = {"X-DagSentry-Token": "test-ingest-token"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/api/v1/failure-events", json=event_payload(), headers=headers
            )
            second = await client.post(
                "/api/v1/failure-events", json=event_payload(), headers=headers
            )
        return first, second

    first, second = asyncio.run(request_twice())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["created"] is True
    assert second.json() == {**first.json(), "created": False}


def test_ingest_api_restart_preserves_idempotency(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    async def request(app: object) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events",
                json=event_payload(),
                headers={"X-DagSentry-Token": "test-ingest-token"},
            )

    first = asyncio.run(request(create_app(settings, session_factory)))
    restarted = asyncio.run(request(create_app(settings, session_factory)))

    assert first.status_code == 200
    assert restarted.status_code == 200
    assert restarted.json() == {**first.json(), "created": False}


def test_ingest_api_rejects_invalid_payload_without_writes(
    settings: Settings, session_factory: SessionFactory
) -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events",
                json={**event_payload(), "try_number": 0},
                headers={"X-DagSentry-Token": "test-ingest-token"},
            )

    response = asyncio.run(request())

    with session_factory() as session:
        event_count = session.scalar(select(func.count()).select_from(FailureEventRecord))
        outbox_count = session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord))
    assert response.status_code == 422
    assert event_count == 0
    assert outbox_count == 0


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {key: value for key, value in event_payload().items() if key != "task_id"},
        {**event_payload(), "map_index": -2},
        {**event_payload(), "try_number": 0},
        {**event_payload(), "unexpected": "field"},
        {**event_payload(), "dag_id": "x" * 251},
    ],
    ids=["missing-field", "map-index", "try-number", "extra-field", "long-identifier"],
)
def test_ingest_api_validation_matrix_does_not_write(
    settings: Settings,
    session_factory: SessionFactory,
    invalid_payload: dict[str, object],
) -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events",
                json=invalid_payload,
                headers={"X-DagSentry-Token": "test-ingest-token"},
            )

    response = asyncio.run(request())

    assert response.status_code == 422
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 0
        assert session.scalar(select(func.count()).select_from(DiagnosisOutboxRecord)) == 0


def test_ingest_api_rejects_oversized_payload_before_writing(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    settings.ingest_max_request_bytes = 512

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events",
                json={**event_payload(), "padding": "x" * 1000},
                headers={"X-DagSentry-Token": "test-ingest-token"},
            )

    response = asyncio.run(request())

    assert response.status_code == 413
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 0


def test_ingest_api_enforces_size_limit_when_content_length_is_incorrect(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    settings.ingest_max_request_bytes = 512

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(settings, session_factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/failure-events",
                content=LargeBodyWithoutContentLength(),
                headers={
                    "Content-Type": "application/json",
                    "X-DagSentry-Token": "test-ingest-token",
                },
            )

    response = asyncio.run(request())

    assert response.status_code == 413
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FailureEventRecord)) == 0
