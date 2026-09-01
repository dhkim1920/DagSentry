from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
import pytest

from dagsentry.airflow.collector import (
    CollectorClient,
    CollectorConfigurationError,
    CollectorSettings,
    build_failure_event,
    collect_failure,
    collect_retry_failure,
    normalized_task_state,
)
from dagsentry.domain.failure_event import CollectionSource, FailureState


@dataclass
class FakeTaskInstance:
    dag_id: str = "daily_orders"
    task_id: str = "load_orders"
    run_id: str = "scheduled__2026-08-09T00:00:00+00:00"
    map_index: int | None = -1
    try_number: int = 2
    end_date: datetime | None = datetime(2026, 8, 9, 0, 1, tzinfo=UTC)
    operator_name: str | None = "PythonOperator"
    state: object = "failed"


def settings() -> CollectorSettings:
    return CollectorSettings(
        environment="production",
        ingest_url="http://dagsentry:8000/api/v1/failure-events",
        ingest_api_token="test-token",
    )


def test_build_failure_event_uses_current_try_and_map_index() -> None:
    event = build_failure_event(
        FakeTaskInstance(map_index=3, try_number=4),
        settings=settings(),
        source=CollectionSource.RETRY_CALLBACK,
        state=FailureState.UP_FOR_RETRY,
    )

    assert event.dag_run_id == "scheduled__2026-08-09T00:00:00+00:00"
    assert event.map_index == 3
    assert event.try_number == 4
    assert event.observed_at == datetime(2026, 8, 9, 0, 1, tzinfo=UTC)
    assert event.operator_type == "PythonOperator"


def test_missing_map_index_and_end_date_are_normalized() -> None:
    before = datetime.now(UTC)
    event = build_failure_event(
        FakeTaskInstance(map_index=None, end_date=None),
        settings=settings(),
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
    )
    after = datetime.now(UTC)

    assert event.map_index == -1
    assert before <= event.observed_at <= after


def test_client_sends_auth_correlation_and_json_payload() -> None:
    seen_requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200)

    client = CollectorClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    event = build_failure_event(
        FakeTaskInstance(),
        settings=settings(),
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
    )

    client.send(event)

    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.headers["X-DagSentry-Token"] == "test-token"
    assert request.headers["X-Correlation-ID"]
    assert request.url == "http://dagsentry:8000/api/v1/failure-events"
    assert b'"try_number":2' in request.content
    assert b'"source":"LISTENER"' in request.content


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_client_retries_transient_http_status(status_code: int) -> None:
    attempts = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code if attempts == 1 else 200)

    client = CollectorClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    event = build_failure_event(
        FakeTaskInstance(),
        settings=settings(),
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
    )

    client.send(event)

    assert attempts == 2


def test_client_does_not_retry_permanent_http_error() -> None:
    attempts = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401)

    client = CollectorClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    event = build_failure_event(
        FakeTaskInstance(),
        settings=settings(),
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.send(event)
    assert attempts == 1


def test_collection_error_does_not_escape_airflow(caplog: pytest.LogCaptureFixture) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable")

    client = CollectorClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    delivered = collect_failure(
        FakeTaskInstance(),
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
        client=client,
    )

    assert delivered is False
    assert "could not deliver" in caplog.text
    assert "test-token" not in caplog.text
    assert getattr(caplog.records[-1], "dagsentry_source") == "LISTENER"
    assert getattr(caplog.records[-1], "try_number") == 2


def test_retry_callback_sends_up_for_retry_event(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_events: list[object] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent_events.append(request)
        return httpx.Response(200)

    client = CollectorClient(
        settings(),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr("dagsentry.airflow.collector.get_default_client", lambda: client)

    delivered = collect_retry_failure({"ti": FakeTaskInstance(try_number=3)})

    assert delivered is True
    request = sent_events[0]
    assert isinstance(request, httpx.Request)
    assert b'"source":"RETRY_CALLBACK"' in request.content
    assert b'"state":"UP_FOR_RETRY"' in request.content
    assert b'"try_number":3' in request.content


def test_settings_validate_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAGSENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DAGSENTRY_INGEST_URL", raising=False)
    monkeypatch.delenv("DAGSENTRY_INGEST_API_TOKEN", raising=False)

    with pytest.raises(CollectorConfigurationError, match="DAGSENTRY_ENVIRONMENT"):
        CollectorSettings.from_environment()

    assert "test-token" not in repr(settings())


class FakeState(StrEnum):
    FAILED = "failed"


@pytest.mark.parametrize("state", ["failed", "FAILED", FakeState.FAILED])
def test_task_state_is_normalized(state: Any) -> None:
    assert normalized_task_state(FakeTaskInstance(state=state)) == "FAILED"
