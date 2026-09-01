"""Airflow 3 Public REST API client for one failed Task Try log."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import quote, urlparse
from uuid import UUID

import httpx

from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.models import FailureEventRecord

_MAX_LOG_PAGES = 100
_SOURCE_GROUP_START = "::group::Log message source details"
_SOURCE_GROUP_END = "::endgroup::"


class LogCollectionStatus(StrEnum):
    """Whether a Task Try log is available for diagnosis."""

    AVAILABLE = "AVAILABLE"
    LOG_UNAVAILABLE = "LOG_UNAVAILABLE"


class LogUnavailableReason(StrEnum):
    """Stable reasons that log collection could not produce usable content."""

    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    TASK_TRY_NOT_FOUND = "TASK_TRY_NOT_FOUND"
    REMOTE_LOG_UNAVAILABLE = "REMOTE_LOG_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    AIRFLOW_UNAVAILABLE = "AIRFLOW_UNAVAILABLE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


@dataclass(frozen=True)
class TaskLogReference:
    """Exact Airflow Task Try whose log must be fetched."""

    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int

    def __post_init__(self) -> None:
        if self.map_index < -1:
            raise ValueError("map_index must be at least -1")
        if self.try_number < 1:
            raise ValueError("try_number must be at least 1")


@dataclass(frozen=True)
class TaskLogResult:
    """Normalized result of reading all bounded Airflow log chunks."""

    status: LogCollectionStatus
    content: str | None
    unavailable_reason: LogUnavailableReason | None
    response_bytes: int
    page_count: int

    def __post_init__(self) -> None:
        if self.response_bytes < 0 or self.page_count < 0:
            raise ValueError("log result counters must not be negative")
        if self.status == LogCollectionStatus.AVAILABLE:
            if self.content is None or self.unavailable_reason is not None:
                raise ValueError("available log result must contain only log content")
        elif self.content is not None or self.unavailable_reason is None:
            raise ValueError("unavailable log result must contain only an unavailable reason")


@dataclass(frozen=True)
class AirflowLogClientConfig:
    """Connection and safety limits for the Airflow API client."""

    base_url: str
    api_token: str
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.1
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Airflow API base URL must be an HTTP(S) URL")
        if not self.api_token:
            raise ValueError("Airflow API token must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")

    def __repr__(self) -> str:
        return (
            f"AirflowLogClientConfig(base_url={self.base_url!r}, api_token='**********', "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r}, "
            f"max_response_bytes={self.max_response_bytes!r})"
        )


class TaskLogFetcher(Protocol):
    """Boundary consumed by the future diagnosis pipeline."""

    def fetch(self, reference: TaskLogReference) -> TaskLogResult:
        """Fetch one exact Task Try log."""


class FailureEventNotFoundError(LookupError):
    """Raised when an Outbox job references a missing Failure Event."""


class FailureTaskLogLoader:
    """Resolve a stored Failure Event into the exact Airflow log request."""

    def __init__(self, session_factory: SessionFactory, fetcher: TaskLogFetcher) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher

    def load(self, failure_event_id: UUID) -> TaskLogResult:
        """Load one Failure Event and fetch only its corresponding Task Try log."""
        with self.session_factory() as session:
            event = session.get(FailureEventRecord, failure_event_id)
            if event is None:
                raise FailureEventNotFoundError(str(failure_event_id))
            reference = TaskLogReference(
                dag_id=event.dag_id,
                dag_run_id=event.dag_run_id,
                task_id=event.task_id,
                map_index=event.map_index,
                try_number=event.try_number,
            )
        return self.fetcher.fetch(reference)


class AirflowLogClient:
    """Fetch bounded, paginated Task Try logs from Airflow's public API."""

    def __init__(
        self,
        config: AirflowLogClientConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=config.timeout_seconds,
        )
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> AirflowLogClient:
        """Build a client from the Worker process environment settings."""
        if settings.airflow_api_base_url is None or settings.airflow_api_token is None:
            raise ValueError("Airflow API settings are not configured")
        return cls(
            AirflowLogClientConfig(
                base_url=settings.airflow_api_base_url,
                api_token=settings.airflow_api_token.get_secret_value(),
                timeout_seconds=settings.airflow_api_timeout_seconds,
                max_attempts=settings.airflow_api_max_attempts,
                retry_backoff_seconds=settings.airflow_api_retry_backoff_seconds,
                max_response_bytes=settings.airflow_log_max_response_bytes,
            )
        )

    def fetch(self, reference: TaskLogReference) -> TaskLogResult:
        """Fetch all log pages in order without exceeding the configured byte limit."""
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        response_bytes = 0
        page_count = 0
        text_parts: list[str] = []
        inside_source_group = False
        has_log_content = False

        while page_count < _MAX_LOG_PAGES:
            page = self._request_page(
                reference,
                continuation_token,
                remaining_bytes=self.config.max_response_bytes - response_bytes,
                response_bytes=response_bytes,
                page_count=page_count,
            )
            if isinstance(page, TaskLogResult):
                return page

            body, body_size = page
            response_bytes += body_size
            page_count += 1
            parsed = _parse_page(body, inside_source_group)
            if parsed is None:
                return _unavailable(
                    LogUnavailableReason.INVALID_RESPONSE, response_bytes, page_count
                )
            page_parts, continuation_token, inside_source_group, page_has_log = parsed
            text_parts.extend(page_parts)
            has_log_content = has_log_content or page_has_log

            if continuation_token is None:
                if not has_log_content:
                    return _unavailable(
                        LogUnavailableReason.REMOTE_LOG_UNAVAILABLE,
                        response_bytes,
                        page_count,
                    )
                return TaskLogResult(
                    status=LogCollectionStatus.AVAILABLE,
                    content="".join(text_parts),
                    unavailable_reason=None,
                    response_bytes=response_bytes,
                    page_count=page_count,
                )
            if continuation_token in seen_tokens:
                return _unavailable(
                    LogUnavailableReason.INVALID_RESPONSE, response_bytes, page_count
                )
            seen_tokens.add(continuation_token)

        return _unavailable(LogUnavailableReason.INVALID_RESPONSE, response_bytes, page_count)

    def _request_page(
        self,
        reference: TaskLogReference,
        continuation_token: str | None,
        *,
        remaining_bytes: int,
        response_bytes: int,
        page_count: int,
    ) -> tuple[bytes, int] | TaskLogResult:
        url = _task_log_url(self.config.base_url, reference)
        params: dict[str, str | int | bool] = {
            "full_content": False,
            "map_index": reference.map_index,
        }
        if continuation_token is not None:
            params["token"] = continuation_token
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.api_token}",
        }

        for attempt in range(self.config.max_attempts):
            try:
                with self.http_client.stream(
                    "GET",
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                ) as response:
                    reason = _status_reason(response.status_code)
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt + 1 < self.config.max_attempts:
                        self.sleep(self.config.retry_backoff_seconds)
                        continue
                    if reason is not None:
                        return _unavailable(reason, response_bytes, page_count)
                    body = _read_limited_body(response, remaining_bytes)
                    if body is None:
                        return _unavailable(
                            LogUnavailableReason.RESPONSE_TOO_LARGE,
                            response_bytes,
                            page_count,
                        )
                    return body, len(body)
            except httpx.TimeoutException:
                if attempt + 1 == self.config.max_attempts:
                    return _unavailable(LogUnavailableReason.TIMEOUT, response_bytes, page_count)
                self.sleep(self.config.retry_backoff_seconds)
            except httpx.TransportError:
                if attempt + 1 == self.config.max_attempts:
                    return _unavailable(
                        LogUnavailableReason.AIRFLOW_UNAVAILABLE,
                        response_bytes,
                        page_count,
                    )
                self.sleep(self.config.retry_backoff_seconds)

        raise RuntimeError("unreachable Airflow log request state")  # pragma: no cover


def _task_log_url(base_url: str, reference: TaskLogReference) -> str:
    segments = (reference.dag_id, reference.dag_run_id, reference.task_id)
    dag_id, dag_run_id, task_id = (quote(segment, safe="") for segment in segments)
    return (
        f"{base_url.rstrip('/')}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/"
        f"taskInstances/{task_id}/logs/{reference.try_number}"
    )


def _status_reason(status_code: int) -> LogUnavailableReason | None:
    if 200 <= status_code < 300:
        return None
    if status_code == 401:
        return LogUnavailableReason.AUTHENTICATION
    if status_code == 403:
        return LogUnavailableReason.AUTHORIZATION
    if status_code == 404:
        return LogUnavailableReason.TASK_TRY_NOT_FOUND
    if status_code == 400:
        return LogUnavailableReason.REMOTE_LOG_UNAVAILABLE
    return LogUnavailableReason.AIRFLOW_UNAVAILABLE


def _read_limited_body(response: httpx.Response, remaining_bytes: int) -> bytes | None:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > remaining_bytes:
                return None
        except ValueError:
            pass

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > remaining_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_page(
    body: bytes, inside_source_group: bool
) -> tuple[list[str], str | None, bool, bool] | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    continuation_token = payload.get("continuation_token")
    if not isinstance(content, list) or not (
        continuation_token is None or isinstance(continuation_token, str)
    ):
        return None

    if all(isinstance(item, str) for item in content):
        parts = [item for item in content if isinstance(item, str)]
        return parts, continuation_token, inside_source_group, any(part.strip() for part in parts)
    if not all(isinstance(item, dict) for item in content):
        return None

    parts = []
    has_log_content = False
    for item in content:
        event = item.get("event")
        if not isinstance(event, str):
            return None
        parts.append(f"{event}\n")
        error_detail_parts = _error_detail_parts(item)
        parts.extend(error_detail_parts)
        if event == _SOURCE_GROUP_START:
            inside_source_group = True
        elif event == _SOURCE_GROUP_END:
            inside_source_group = False
        elif not inside_source_group and (event.strip() or error_detail_parts):
            has_log_content = True
    return parts, continuation_token, inside_source_group, has_log_content


def _error_detail_parts(item: dict[str, object]) -> list[str]:
    detail = item.get("error_detail")
    if not isinstance(detail, list):
        return []

    parts: list[str] = []
    for exception in detail:
        if not isinstance(exception, dict):
            continue
        frames = exception.get("frames")
        if isinstance(frames, list):
            parts.extend(
                frame_part for frame in frames if (frame_part := _frame_part(frame)) is not None
            )

        exc_type = exception.get("exc_type")
        exc_value = exception.get("exc_value")
        if isinstance(exc_type, str) and exc_type:
            suffix = f": {exc_value}" if isinstance(exc_value, str) and exc_value else ""
            parts.append(f"{exc_type}{suffix}\n")
        elif isinstance(exc_value, str) and exc_value:
            parts.append(f"{exc_value}\n")
    return parts


def _frame_part(frame: object) -> str | None:
    if not isinstance(frame, dict):
        return None
    filename = frame.get("filename")
    lineno = frame.get("lineno")
    name = frame.get("name")
    if (
        not isinstance(filename, str)
        or not isinstance(lineno, int)
        or isinstance(lineno, bool)
        or not isinstance(name, str)
    ):
        return None
    return f'  File "{filename}", line {lineno}, in {name}\n'


def _unavailable(
    reason: LogUnavailableReason, response_bytes: int, page_count: int
) -> TaskLogResult:
    return TaskLogResult(
        status=LogCollectionStatus.LOG_UNAVAILABLE,
        content=None,
        unavailable_reason=reason,
        response_bytes=response_bytes,
        page_count=page_count,
    )
