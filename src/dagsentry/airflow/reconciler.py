"""Recover Failure Events missed by Airflow's real-time collection paths."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from dagsentry.airflow.collector import CollectorClient, CollectorSettings
from dagsentry.domain.failure_event import (
    CollectionSource,
    FailureEventCreate,
    FailureState,
    make_event_key,
)

logger = logging.getLogger(__name__)


class ReconcilerConfigurationError(ValueError):
    """The Airflow-side Reconciler configuration is invalid."""


class ReconcilerResponseError(RuntimeError):
    """The Airflow API returned an unusable response."""


class WatermarkStore(Protocol):
    """Persistent boundary used to resume a completed reconciliation window."""

    def load(self) -> datetime | None:
        """Return the last completed upper boundary, if one exists."""

    def save(self, value: datetime) -> None:
        """Persist a completed upper boundary."""


class FailureEventSender(Protocol):
    """Existing Failure Ingest boundary reused by the Reconciler."""

    def send(self, event: FailureEventCreate) -> None:
        """Send one Failure Event through the normal ingestion contract."""


class TaskHistoryReader(Protocol):
    """Airflow TaskInstance and Try history query boundary."""

    def iter_task_instances(
        self,
        *,
        updated_at_gte: datetime,
        updated_at_lt: datetime,
    ) -> Iterable[TaskInstanceReference]:
        """Return TaskInstances updated inside a fixed window."""

    def task_tries(self, reference: TaskInstanceReference) -> list[TaskTryHistory]:
        """Return historical Tries for one TaskInstance."""


@dataclass(frozen=True)
class ReconcilerSettings:
    """Bounded Airflow API and scan-window settings."""

    collector: CollectorSettings
    airflow_api_base_url: str
    airflow_api_token: str
    request_timeout_seconds: float = 10.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.5
    page_size: int = 100
    overlap: timedelta = timedelta(minutes=5)
    initial_lookback: timedelta = timedelta(days=1)
    watermark_key: str = "dagsentry_reconciler_watermark"

    def __post_init__(self) -> None:
        parsed_url = urlparse(self.airflow_api_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ReconcilerConfigurationError(
                "DAGSENTRY_AIRFLOW_API_BASE_URL must be an HTTP(S) URL"
            )
        if not self.airflow_api_token:
            raise ReconcilerConfigurationError("DAGSENTRY_AIRFLOW_API_TOKEN is empty")
        if self.request_timeout_seconds <= 0:
            raise ReconcilerConfigurationError("Reconciler timeout must be positive")
        if self.max_attempts < 1:
            raise ReconcilerConfigurationError("Reconciler max attempts must be positive")
        if self.retry_backoff_seconds < 0:
            raise ReconcilerConfigurationError("Reconciler backoff must not be negative")
        if not 1 <= self.page_size <= 1000:
            raise ReconcilerConfigurationError("Reconciler page size must be between 1 and 1000")
        if self.overlap < timedelta(0):
            raise ReconcilerConfigurationError("Reconciler overlap must not be negative")
        if self.initial_lookback <= timedelta(0):
            raise ReconcilerConfigurationError("Reconciler initial lookback must be positive")
        if not self.watermark_key:
            raise ReconcilerConfigurationError("Reconciler watermark key must not be empty")

    def __repr__(self) -> str:
        return (
            f"ReconcilerSettings(collector={self.collector!r}, "
            f"airflow_api_base_url={self.airflow_api_base_url!r}, "
            "airflow_api_token='**********', "
            f"request_timeout_seconds={self.request_timeout_seconds!r}, "
            f"max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r}, "
            f"page_size={self.page_size!r}, overlap={self.overlap!r}, "
            f"initial_lookback={self.initial_lookback!r}, "
            f"watermark_key={self.watermark_key!r})"
        )

    @classmethod
    def from_environment(cls) -> ReconcilerSettings:
        """Load Airflow-side settings without accessing its Metadata DB."""
        base_url = os.environ.get("DAGSENTRY_AIRFLOW_API_BASE_URL")
        token = os.environ.get("DAGSENTRY_AIRFLOW_API_TOKEN")
        missing = [
            name
            for name, value in (
                ("DAGSENTRY_AIRFLOW_API_BASE_URL", base_url),
                ("DAGSENTRY_AIRFLOW_API_TOKEN", token),
            )
            if not value
        ]
        if missing:
            raise ReconcilerConfigurationError(
                f"Missing DagSentry Reconciler settings: {', '.join(missing)}"
            )
        assert base_url is not None and token is not None
        environment = os.environ.get("DAGSENTRY_ENVIRONMENT", "unknown")
        return cls(
            collector=CollectorSettings.from_environment(),
            airflow_api_base_url=base_url,
            airflow_api_token=token,
            request_timeout_seconds=_float_environment(
                "DAGSENTRY_RECONCILER_TIMEOUT_SECONDS", 10.0
            ),
            max_attempts=_int_environment("DAGSENTRY_RECONCILER_MAX_ATTEMPTS", 3),
            retry_backoff_seconds=_float_environment(
                "DAGSENTRY_RECONCILER_RETRY_BACKOFF_SECONDS", 0.5
            ),
            page_size=_int_environment("DAGSENTRY_RECONCILER_PAGE_SIZE", 100),
            overlap=timedelta(
                seconds=_float_environment("DAGSENTRY_RECONCILER_OVERLAP_SECONDS", 300.0)
            ),
            initial_lookback=timedelta(
                seconds=_float_environment(
                    "DAGSENTRY_RECONCILER_INITIAL_LOOKBACK_SECONDS", 86_400.0
                )
            ),
            watermark_key=os.environ.get(
                "DAGSENTRY_RECONCILER_WATERMARK_KEY",
                f"dagsentry_reconciler_watermark_{environment}",
            ),
        )


class TaskInstanceReference(BaseModel):
    """Current TaskInstance identity returned by the Airflow collection endpoint."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int


class TaskTryHistory(BaseModel):
    """Historical Task Try fields required by the Failure Ingest contract."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    state: str | None
    end_date: datetime | None
    operator: str | None = None
    operator_name: str | None = None

    @field_validator("end_date")
    @classmethod
    def normalize_end_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Task Try end_date must include a timezone")
        return value.astimezone(UTC)


class AirflowVariableWatermarkStore:
    """Watermark adapter using Airflow's public Task SDK Variable interface."""

    def __init__(self, key: str) -> None:
        self.key = key

    def load(self) -> datetime | None:
        from airflow.sdk import Variable

        raw = Variable.get(self.key, default=None)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ReconcilerConfigurationError("Reconciler watermark must be an ISO 8601 string")
        return _utc_datetime(datetime.fromisoformat(raw))

    def save(self, value: datetime) -> None:
        from airflow.sdk import Variable

        Variable.set(self.key, _utc_datetime(value).isoformat())


@dataclass(frozen=True)
class ReconciliationResult:
    """Summary of one fully completed scan window."""

    window_start: datetime
    window_end: datetime
    task_instances_scanned: int
    task_tries_scanned: int
    failure_events_sent: int


class AirflowTaskHistoryClient:
    """Bounded client for Airflow's public TaskInstance and Try history endpoints."""

    def __init__(
        self,
        settings: ReconcilerSettings,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.http_client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=settings.request_timeout_seconds,
        )
        self.sleep = sleep

    def iter_task_instances(
        self,
        *,
        updated_at_gte: datetime,
        updated_at_lt: datetime,
    ) -> Iterator[TaskInstanceReference]:
        """Iterate a fixed update window using Airflow cursor pagination."""
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            body = self._get_json(
                "/api/v2/dags/~/dagRuns/~/taskInstances",
                params={
                    "cursor": cursor,
                    "updated_at_gte": _utc_datetime(updated_at_gte).isoformat(),
                    "updated_at_lt": _utc_datetime(updated_at_lt).isoformat(),
                    "limit": self.settings.page_size,
                    "order_by": "id",
                },
            )
            raw_items = body.get("task_instances")
            if not isinstance(raw_items, list):
                raise ReconcilerResponseError("Airflow task_instances must be a list")
            for raw_item in raw_items:
                yield TaskInstanceReference.model_validate(raw_item)

            next_cursor = body.get("next_cursor")
            if next_cursor is None:
                return
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ReconcilerResponseError("Airflow next_cursor is invalid")
            if next_cursor in seen_cursors:
                raise ReconcilerResponseError("Airflow cursor pagination repeated a cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def task_tries(self, reference: TaskInstanceReference) -> list[TaskTryHistory]:
        """Return every historical Try for an exact mapped TaskInstance."""
        path = (
            f"/api/v2/dags/{quote(reference.dag_id, safe='')}/dagRuns/"
            f"{quote(reference.dag_run_id, safe='')}/taskInstances/"
            f"{quote(reference.task_id, safe='')}/tries"
        )
        body = self._get_json(path, params={"map_index": reference.map_index})
        raw_items = body.get("task_instances")
        if not isinstance(raw_items, list):
            raise ReconcilerResponseError("Airflow Try task_instances must be a list")
        return [TaskTryHistory.model_validate(raw_item) for raw_item in raw_items]

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None],
    ) -> dict[str, object]:
        url = f"{self.settings.airflow_api_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self.settings.airflow_api_token}"}
        for attempt in range(self.settings.max_attempts):
            try:
                response = self.http_client.get(url, headers=headers, params=params)
            except (httpx.NetworkError, httpx.TimeoutException):
                if attempt + 1 == self.settings.max_attempts:
                    raise
                self.sleep(self.settings.retry_backoff_seconds)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.settings.max_attempts:
                    self.sleep(self.settings.retry_backoff_seconds)
                    continue
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ReconcilerResponseError("Airflow response must be a JSON object")
            return body
        raise RuntimeError("Unreachable Reconciler retry state")  # pragma: no cover


class FailureReconciler:
    """Replay missed historical failure Tries through the normal Ingest API."""

    def __init__(
        self,
        settings: ReconcilerSettings,
        *,
        airflow_client: TaskHistoryReader,
        event_sender: FailureEventSender,
        watermark_store: WatermarkStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.airflow_client = airflow_client
        self.event_sender = event_sender
        self.watermark_store = watermark_store
        self.clock = clock

    def run(self) -> ReconciliationResult:
        """Complete one fixed window and advance its watermark only after every send."""
        window_end = _utc_datetime(self.clock())
        watermark = self.watermark_store.load()
        if watermark is None:
            window_start = window_end - self.settings.initial_lookback
        else:
            normalized_watermark = _utc_datetime(watermark)
            window_start = min(normalized_watermark, window_end) - self.settings.overlap

        task_instances_scanned = 0
        task_tries_scanned = 0
        failure_events_sent = 0
        sent_keys: set[str] = set()
        for reference in self.airflow_client.iter_task_instances(
            updated_at_gte=window_start,
            updated_at_lt=window_end,
        ):
            task_instances_scanned += 1
            for task_try in self.airflow_client.task_tries(reference):
                task_tries_scanned += 1
                event = _failure_event(task_try, environment=self.settings.collector.environment)
                if event is None:
                    continue
                event_key = make_event_key(event.identity())
                if event_key in sent_keys:
                    continue
                self.event_sender.send(event)
                sent_keys.add(event_key)
                failure_events_sent += 1

        self.watermark_store.save(window_end)
        return ReconciliationResult(
            window_start=window_start,
            window_end=window_end,
            task_instances_scanned=task_instances_scanned,
            task_tries_scanned=task_tries_scanned,
            failure_events_sent=failure_events_sent,
        )


def run_reconciliation_from_airflow() -> ReconciliationResult:
    """Build the configured Airflow DAG task implementation."""
    settings = ReconcilerSettings.from_environment()
    airflow_client = AirflowTaskHistoryClient(settings)
    event_sender = CollectorClient(settings.collector)
    reconciler = FailureReconciler(
        settings,
        airflow_client=airflow_client,
        event_sender=event_sender,
        watermark_store=AirflowVariableWatermarkStore(settings.watermark_key),
    )
    result = reconciler.run()
    logger.info(
        "DagSentry reconciliation completed",
        extra={
            "window_start": result.window_start.isoformat(),
            "window_end": result.window_end.isoformat(),
            "task_instances_scanned": result.task_instances_scanned,
            "task_tries_scanned": result.task_tries_scanned,
            "failure_events_sent": result.failure_events_sent,
        },
    )
    return result


def _failure_event(task_try: TaskTryHistory, *, environment: str) -> FailureEventCreate | None:
    state_value = task_try.state.lower() if task_try.state is not None else None
    if state_value == "failed":
        state = FailureState.FAILED
    elif state_value == "up_for_retry":
        state = FailureState.UP_FOR_RETRY
    else:
        return None
    if task_try.end_date is None:
        raise ReconcilerResponseError("Failed Task Try is missing end_date")
    return FailureEventCreate(
        environment=environment,
        dag_id=task_try.dag_id,
        dag_run_id=task_try.dag_run_id,
        task_id=task_try.task_id,
        map_index=task_try.map_index,
        try_number=task_try.try_number,
        source=CollectionSource.RECONCILER,
        state=state,
        observed_at=task_try.end_date,
        operator_type=task_try.operator_name or task_try.operator,
    )


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReconcilerConfigurationError("Reconciler timestamps must include a timezone")
    return value.astimezone(UTC)


def _int_environment(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ReconcilerConfigurationError(f"{name} must be an integer") from error


def _float_environment(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ReconcilerConfigurationError(f"{name} must be a number") from error
