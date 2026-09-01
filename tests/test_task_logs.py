from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from dagsentry.db import SessionFactory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.ingestion import ingest_failure_event
from dagsentry.task_logs import (
    AirflowLogClient,
    AirflowLogClientConfig,
    FailureEventNotFoundError,
    FailureTaskLogLoader,
    LogCollectionStatus,
    LogUnavailableReason,
    TaskLogReference,
    TaskLogResult,
)


def reference() -> TaskLogReference:
    return TaskLogReference(
        dag_id="daily/orders",
        dag_run_id="scheduled__2026-08-09T00:00:00+00:00",
        task_id="load orders",
        map_index=3,
        try_number=2,
    )


def client(
    handler: Any,
    *,
    max_response_bytes: int = 1_048_576,
    max_attempts: int = 2,
) -> AirflowLogClient:
    return AirflowLogClient(
        AirflowLogClientConfig(
            base_url="http://airflow:8080/",
            api_token="secret-token",
            max_response_bytes=max_response_bytes,
            max_attempts=max_attempts,
            retry_backoff_seconds=0,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )


def response(content: list[object], token: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"content": content, "continuation_token": token})


def test_fetch_uses_exact_try_map_index_auth_and_encoded_path() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response([{"timestamp": None, "event": "traceback"}])

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.AVAILABLE
    assert result.content == "traceback\n"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.raw_path == (
        b"/api/v2/dags/daily%2Forders/dagRuns/"
        b"scheduled__2026-08-09T00%3A00%3A00%2B00%3A00/"
        b"taskInstances/load%20orders/logs/2?full_content=false&map_index=3"
    )
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["Accept"] == "application/json"


def test_fetch_combines_paginated_chunks_in_order() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return response([{"event": "first"}], "next-token")
        return response([{"event": "second"}])

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.AVAILABLE
    assert result.content == "first\nsecond\n"
    assert result.page_count == 2
    assert "token" not in requests[0].url.params
    assert requests[1].url.params["token"] == "next-token"
    assert requests[1].url.params["map_index"] == "3"


def test_fetch_preserves_legacy_string_chunks() -> None:
    result = client(lambda _: response(["first\n", "second\n"])).fetch(reference())

    assert result.status == LogCollectionStatus.AVAILABLE
    assert result.content == "first\nsecond\n"


def test_fetch_includes_airflow_error_detail_in_log_content() -> None:
    result = client(
        lambda _: response(
            [
                {
                    "event": "Task failed with exception",
                    "error_detail": [
                        {
                            "exc_type": "ConnectionError",
                            "exc_value": "warehouse gateway refused connection",
                            "frames": [
                                {
                                    "filename": "/opt/airflow/dags/orders.py",
                                    "lineno": 42,
                                    "name": "load_orders",
                                }
                            ],
                        }
                    ],
                }
            ]
        )
    ).fetch(reference())

    assert result.status == LogCollectionStatus.AVAILABLE
    assert result.content == (
        "Task failed with exception\n"
        '  File "/opt/airflow/dags/orders.py", line 42, in load_orders\n'
        "ConnectionError: warehouse gateway refused connection\n"
    )


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (400, LogUnavailableReason.REMOTE_LOG_UNAVAILABLE),
        (401, LogUnavailableReason.AUTHENTICATION),
        (403, LogUnavailableReason.AUTHORIZATION),
        (404, LogUnavailableReason.TASK_TRY_NOT_FOUND),
    ],
)
def test_permanent_http_failures_are_normalized_without_retry(
    status_code: int, reason: LogUnavailableReason
) -> None:
    attempts = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code)

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == reason
    assert attempts == 1


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_transient_http_failures_are_retried(status_code: int) -> None:
    attempts = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code)
        return response([{"event": "recovered"}])

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.AVAILABLE
    assert attempts == 2


def test_timeout_is_retried_then_normalized() -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.TIMEOUT
    assert attempts == 2


def test_network_failure_is_retried_then_normalized() -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    result = client(handle).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.AIRFLOW_UNAVAILABLE
    assert attempts == 2


def test_response_size_limit_is_enforced_while_reading() -> None:
    body = json.dumps({"content": ["too large"], "continuation_token": None}).encode()

    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    result = client(handle, max_response_bytes=len(body) - 1).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.RESPONSE_TOO_LARGE
    assert result.content is None


def test_source_details_without_log_content_are_unavailable() -> None:
    result = client(
        lambda _: response(
            [
                {"event": "::group::Log message source details"},
                {"event": "Log file not found on worker 'worker-1'."},
                {"event": "::endgroup::"},
            ]
        )
    ).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.REMOTE_LOG_UNAVAILABLE


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"content": [{"missing_event": "value"}], "continuation_token": None}).encode(),
        json.dumps({"content": [], "continuation_token": 123}).encode(),
    ],
)
def test_invalid_response_is_normalized(payload: bytes) -> None:
    result = client(lambda _: httpx.Response(200, content=payload)).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.INVALID_RESPONSE


def test_repeated_continuation_token_is_rejected() -> None:
    result = client(lambda _: response([{"event": "line"}], "same-token")).fetch(reference())

    assert result.status == LogCollectionStatus.LOG_UNAVAILABLE
    assert result.unavailable_reason == LogUnavailableReason.INVALID_RESPONSE
    assert result.page_count == 2


def test_config_repr_does_not_expose_token() -> None:
    config = AirflowLogClientConfig("http://airflow:8080", "secret-token")

    assert "secret-token" not in repr(config)


def streaming_response(chunks: list[bytes]) -> httpx.Response:
    class ChunkStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield from chunks

    return httpx.Response(200, stream=ChunkStream())


def test_streamed_response_stops_after_limit() -> None:
    first = b'{"content":["'
    result = client(
        lambda _: streaming_response([first, b"too-large", b'"],"continuation_token":null}']),
        max_response_bytes=len(first) + 2,
    ).fetch(reference())

    assert result.unavailable_reason == LogUnavailableReason.RESPONSE_TOO_LARGE


def test_failure_loader_uses_only_stored_task_try_identity(
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        failure = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="production",
                dag_id="daily_orders",
                dag_run_id="scheduled__2026-08-09T00:00:00+00:00",
                task_id="load_orders",
                map_index=4,
                try_number=3,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime(2026, 8, 9, tzinfo=UTC),
            ),
        )

    class RecordingFetcher:
        def __init__(self) -> None:
            self.references: list[TaskLogReference] = []

        def fetch(self, task_log_reference: TaskLogReference) -> TaskLogResult:
            self.references.append(task_log_reference)
            return TaskLogResult(LogCollectionStatus.AVAILABLE, "log", None, 3, 1)

    fetcher = RecordingFetcher()
    result = FailureTaskLogLoader(session_factory, fetcher).load(failure.failure_event_id)

    assert result.content == "log"
    assert fetcher.references == [
        TaskLogReference(
            dag_id="daily_orders",
            dag_run_id="scheduled__2026-08-09T00:00:00+00:00",
            task_id="load_orders",
            map_index=4,
            try_number=3,
        )
    ]


def test_failure_loader_rejects_unknown_event(session_factory: SessionFactory) -> None:
    class UnusedFetcher:
        def fetch(self, _: TaskLogReference) -> TaskLogResult:
            raise AssertionError("fetch must not be called")

    loader = FailureTaskLogLoader(session_factory, UnusedFetcher())

    with pytest.raises(FailureEventNotFoundError):
        loader.load(UUID(str(uuid4())))
