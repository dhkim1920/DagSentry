"""Build and deliver Failure Events from Airflow public interfaces."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState

logger = logging.getLogger(__name__)

_ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_SEND_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.1
_REQUEST_TIMEOUT_SECONDS = 2.0


class CollectorConfigurationError(ValueError):
    """Raised when an Airflow collector process is not configured."""


class TaskInstanceLike(Protocol):
    """Public TaskInstance fields used by both runtime and API-server listeners."""

    dag_id: str
    task_id: str
    run_id: str
    map_index: int | None
    try_number: int
    end_date: datetime | None


@dataclass(frozen=True)
class CollectorSettings:
    """Environment-backed settings used inside Airflow processes."""

    environment: str
    ingest_url: str
    ingest_api_token: str

    def __post_init__(self) -> None:
        if not _ENVIRONMENT_PATTERN.fullmatch(self.environment):
            raise CollectorConfigurationError("DAGSENTRY_ENVIRONMENT is invalid")
        parsed_url = urlparse(self.ingest_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise CollectorConfigurationError("DAGSENTRY_INGEST_URL must be an HTTP(S) URL")
        if not self.ingest_api_token:
            raise CollectorConfigurationError("DAGSENTRY_INGEST_API_TOKEN is empty")

    def __repr__(self) -> str:
        return (
            f"CollectorSettings(environment={self.environment!r}, "
            f"ingest_url={self.ingest_url!r}, ingest_api_token='**********')"
        )

    @classmethod
    def from_environment(cls) -> CollectorSettings:
        """Load required collector settings without reading Airflow internals."""
        required = (
            "DAGSENTRY_ENVIRONMENT",
            "DAGSENTRY_INGEST_URL",
            "DAGSENTRY_INGEST_API_TOKEN",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise CollectorConfigurationError(
                f"Missing DagSentry collector settings: {', '.join(missing)}"
            )
        return cls(
            environment=os.environ["DAGSENTRY_ENVIRONMENT"],
            ingest_url=os.environ["DAGSENTRY_INGEST_URL"],
            ingest_api_token=os.environ["DAGSENTRY_INGEST_API_TOKEN"],
        )


class CollectorClient:
    """Synchronous, bounded HTTP delivery suitable for Airflow callbacks."""

    def __init__(
        self,
        settings: CollectorSettings,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    def send(self, event: FailureEventCreate) -> None:
        """Send one event, retrying only transient transport and server failures."""
        headers = {
            "X-Correlation-ID": str(uuid4()),
            "X-DagSentry-Token": self.settings.ingest_api_token,
        }
        for attempt in range(_MAX_SEND_ATTEMPTS):
            try:
                response = self.http_client.post(
                    self.settings.ingest_url,
                    headers=headers,
                    json=event.model_dump(mode="json"),
                )
            except (httpx.NetworkError, httpx.TimeoutException):
                if attempt + 1 == _MAX_SEND_ATTEMPTS:
                    raise
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < _MAX_SEND_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
            response.raise_for_status()
            return


def build_failure_event(
    task_instance: TaskInstanceLike,
    *,
    settings: CollectorSettings,
    source: CollectionSource,
    state: FailureState,
) -> FailureEventCreate:
    """Build the shared ingest contract from public TaskInstance fields."""
    map_index = task_instance.map_index if task_instance.map_index is not None else -1
    return FailureEventCreate(
        environment=settings.environment,
        dag_id=task_instance.dag_id,
        dag_run_id=task_instance.run_id,
        task_id=task_instance.task_id,
        map_index=map_index,
        try_number=task_instance.try_number,
        source=source,
        state=state,
        observed_at=_observed_at(task_instance.end_date),
        operator_type=_operator_type(task_instance),
    )


def collect_failure(
    task_instance: TaskInstanceLike,
    *,
    source: CollectionSource,
    state: FailureState,
    client: CollectorClient | None = None,
) -> bool:
    """Safely deliver one Failure Event without breaking an Airflow component."""
    try:
        delivery_client = client or get_default_client()
        event = build_failure_event(
            task_instance,
            settings=delivery_client.settings,
            source=source,
            state=state,
        )
        delivery_client.send(event)
    except Exception:
        logger.exception(
            "DagSentry could not deliver a Failure Event",
            extra={
                "dagsentry_source": source.value,
                "dag_id": getattr(task_instance, "dag_id", None),
                "task_id": getattr(task_instance, "task_id", None),
                "try_number": getattr(task_instance, "try_number", None),
                "map_index": getattr(task_instance, "map_index", None),
            },
        )
        return False
    return True


def collect_retry_failure(context: Mapping[str, object]) -> bool:
    """Airflow `on_retry_callback` that records the failed current Try."""
    task_instance = context.get("task_instance")
    if task_instance is None:
        task_instance = context.get("ti")
    if task_instance is None:
        logger.error("DagSentry retry callback did not receive a TaskInstance")
        return False
    return collect_failure(
        task_instance,  # type: ignore[arg-type]
        source=CollectionSource.RETRY_CALLBACK,
        state=FailureState.UP_FOR_RETRY,
    )


def normalized_task_state(task_instance: object) -> str | None:
    """Return an Airflow state value without depending on an internal Enum type."""
    state = getattr(task_instance, "state", None)
    if state is None:
        return None
    value = getattr(state, "value", state)
    return str(value).upper()


@lru_cache
def get_default_client() -> CollectorClient:
    """Create one reusable HTTP client per Airflow process."""
    return CollectorClient(CollectorSettings.from_environment())


def _observed_at(value: datetime | None) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return datetime.now(UTC)
    return value.astimezone(UTC)


def _operator_type(task_instance: object) -> str | None:
    for attribute in ("operator_name", "operator"):
        value = getattr(task_instance, attribute, None)
        if isinstance(value, str) and value:
            return value

    task = getattr(task_instance, "task", None)
    if task is None:
        return None
    task_type = getattr(task, "task_type", None)
    if isinstance(task_type, str) and task_type:
        return task_type
    return type(task).__name__
