"""Airflow-backed Incident recovery checks and durable recovery notifications."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select

from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.incident import (
    ACTIVE_INCIDENT_STATUSES,
    IncidentStatus,
    IncidentTransitionInitiator,
    validate_incident_transition,
)
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationProviderError,
)
from dagsentry.domain.recovery import (
    RecoveryNotificationPayload,
    RecoveryNotificationProvider,
)
from dagsentry.metrics import increment_counter, record_counter
from dagsentry.models import (
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
    IncidentRecoveryNotificationRecord,
    IncidentStateTransitionRecord,
)

logger = logging.getLogger(__name__)
RECOVERY_DELIVERY_KEY_VERSION = 1
_NOTIFICATION_RETRY_BATCH_SIZE = 100


class RecoveryCheckerError(RuntimeError):
    """The Recovery Checker received unusable state or configuration."""


@dataclass(frozen=True, order=True)
class TaskInstanceReference:
    """Exact Airflow TaskInstance identity linked to an Incident."""

    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int

    def __post_init__(self) -> None:
        if self.map_index < -1:
            raise ValueError("map_index must be at least -1")


class TaskInstanceStateReader(Protocol):
    """Airflow boundary consumed by the Recovery Checker."""

    def get_state(self, reference: TaskInstanceReference) -> str | None:
        """Return the current normalized state for one exact TaskInstance."""


class _TaskInstanceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    state: str | None


class AirflowTaskStateClient:
    """Read exact current TaskInstance states from Airflow's public REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 5.0,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.1,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Airflow API base URL must be an HTTP(S) URL")
        if not api_token:
            raise ValueError("Airflow API token must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.http_client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
        )
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> AirflowTaskStateClient:
        """Build a state client from the shared service configuration."""
        if settings.airflow_api_base_url is None or settings.airflow_api_token is None:
            raise ValueError("Airflow API settings are not configured")
        return cls(
            base_url=settings.airflow_api_base_url,
            api_token=settings.airflow_api_token.get_secret_value(),
            timeout_seconds=settings.airflow_api_timeout_seconds,
            max_attempts=settings.airflow_api_max_attempts,
            retry_backoff_seconds=settings.airflow_api_retry_backoff_seconds,
        )

    def get_state(self, reference: TaskInstanceReference) -> str | None:
        """Return current state using exact task and map-index filters."""
        path = (
            f"/api/v2/dags/{quote(reference.dag_id, safe='')}/dagRuns/"
            f"{quote(reference.dag_run_id, safe='')}/taskInstances"
        )
        body = self._get_json(
            path,
            params={
                "task_id": reference.task_id,
                "map_index": reference.map_index,
                "limit": 2,
            },
        )
        raw_items = body.get("task_instances")
        if not isinstance(raw_items, list) or len(raw_items) != 1:
            raise RecoveryCheckerError("Airflow must return exactly one TaskInstance")
        try:
            item = _TaskInstanceResponse.model_validate(raw_items[0])
        except ValidationError as error:
            raise RecoveryCheckerError("Airflow TaskInstance response is invalid") from error
        returned_reference = TaskInstanceReference(
            dag_id=item.dag_id,
            dag_run_id=item.dag_run_id,
            task_id=item.task_id,
            map_index=item.map_index,
        )
        if returned_reference != reference:
            raise RecoveryCheckerError("Airflow returned a different TaskInstance")
        return item.state.upper() if item.state is not None else None

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str | int],
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self.api_token}"}
        for attempt in range(self.max_attempts):
            try:
                response = self.http_client.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            except (httpx.NetworkError, httpx.TimeoutException):
                if attempt + 1 == self.max_attempts:
                    raise
                self.sleep(self.retry_backoff_seconds)
                continue
            if (response.status_code == 429 or response.status_code >= 500) and (
                attempt + 1 < self.max_attempts
            ):
                self.sleep(self.retry_backoff_seconds)
                continue
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as error:
                raise RecoveryCheckerError("Airflow response is not valid JSON") from error
            if not isinstance(body, dict):
                raise RecoveryCheckerError("Airflow response must be a JSON object")
            return body
        raise RuntimeError("Unreachable Airflow retry state")  # pragma: no cover


@dataclass(frozen=True)
class RecoveryCheckResult:
    """Summary of one Recovery Checker execution."""

    active_incidents_scanned: int
    incidents_recovered: int
    airflow_failures: int
    notifications_delivered: int
    notification_failures: int


class RecoveryChecker:
    """Recover active Incidents only when every linked Airflow task succeeds."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        airflow_client: TaskInstanceStateReader,
        notification_provider: RecoveryNotificationProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.airflow_client = airflow_client
        self.notification_provider = notification_provider
        self.clock = clock

    def run_once(self) -> RecoveryCheckResult:
        """Retry pending notifications and inspect every active Incident."""
        notifications_delivered = 0
        notification_failures = 0
        for retry_delivery_id in self._retryable_notification_ids():
            try:
                if self._deliver_notification(retry_delivery_id):
                    notifications_delivered += 1
            except NotificationProviderError:
                logger.exception("recovery notification delivery failed")
                notification_failures += 1

        active_ids = self._active_incident_ids()
        recovered = 0
        airflow_failures = 0
        for incident_id in active_ids:
            references = self._task_instance_references(incident_id)
            try:
                states = [self.airflow_client.get_state(item) for item in references]
            except (httpx.HTTPError, RecoveryCheckerError):
                logger.exception("Airflow TaskInstance state lookup failed")
                airflow_failures += 1
                continue
            if not states or any(state != "SUCCESS" for state in states):
                continue

            delivery_id = self._record_recovery(incident_id)
            if delivery_id is None:
                continue
            recovered += 1
            try:
                if self._deliver_notification(delivery_id):
                    notifications_delivered += 1
            except NotificationProviderError:
                logger.exception("recovery notification delivery failed")
                notification_failures += 1

        result = RecoveryCheckResult(
            active_incidents_scanned=len(active_ids),
            incidents_recovered=recovered,
            airflow_failures=airflow_failures,
            notifications_delivered=notifications_delivered,
            notification_failures=notification_failures,
        )
        record_counter(
            self.session_factory,
            "dagsentry_recovery_checker_runs_total",
            "degraded" if airflow_failures or notification_failures else "success",
            now=self.clock(),
        )
        return result

    def _active_incident_ids(self) -> list[UUID]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(IncidentRecord.id)
                    .where(IncidentRecord.status.in_(ACTIVE_INCIDENT_STATUSES))
                    .order_by(IncidentRecord.updated_at, IncidentRecord.id)
                )
            )

    def _task_instance_references(self, incident_id: UUID) -> list[TaskInstanceReference]:
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    FailureEventRecord.dag_id,
                    FailureEventRecord.dag_run_id,
                    FailureEventRecord.task_id,
                    FailureEventRecord.map_index,
                )
                .join(
                    IncidentFailureRecord,
                    IncidentFailureRecord.failure_event_id == FailureEventRecord.id,
                )
                .where(IncidentFailureRecord.incident_id == incident_id)
            ).all()
        references = {
            TaskInstanceReference(
                dag_id=dag_id,
                dag_run_id=dag_run_id,
                task_id=task_id,
                map_index=map_index,
            )
            for dag_id, dag_run_id, task_id, map_index in rows
        }
        if not references:  # pragma: no cover - database invariant
            raise RecoveryCheckerError("Incident has no linked Failure Events")
        return sorted(references)

    def _record_recovery(self, incident_id: UUID) -> UUID | None:
        now = _utc_datetime(self.clock())
        with self.session_factory.begin() as session:
            incident = session.scalar(
                select(IncidentRecord).where(IncidentRecord.id == incident_id).with_for_update()
            )
            if incident is None or incident.status not in ACTIVE_INCIDENT_STATUSES:
                return None
            previous = incident.status
            validate_incident_transition(
                previous,
                IncidentStatus.RECOVERED,
                IncidentTransitionInitiator.SYSTEM,
            )
            failure_count = session.scalar(
                select(func.count())
                .select_from(IncidentFailureRecord)
                .where(IncidentFailureRecord.incident_id == incident_id)
            )
            if failure_count is None or failure_count < 1:  # pragma: no cover - database invariant
                raise RecoveryCheckerError("Incident has no linked Failure Events")
            payload = RecoveryNotificationPayload(
                incident_id=incident.id,
                incident_failure_count=failure_count,
                environment=incident.environment,
                dag_id=incident.dag_id,
                task_id=incident.task_id,
                recovered_at=now,
            )
            incident.status = IncidentStatus.RECOVERED
            incident.updated_at = now
            increment_counter(
                session,
                "dagsentry_incident_events_total",
                "recovered",
                now=now,
            )
            session.add(
                IncidentStateTransitionRecord(
                    incident_id=incident.id,
                    previous_status=previous,
                    status=IncidentStatus.RECOVERED,
                    initiator=IncidentTransitionInitiator.SYSTEM,
                    actor="recovery-checker",
                    reason="All linked Airflow TaskInstances are SUCCESS",
                    created_at=now,
                )
            )
            delivery_id = uuid4()
            session.add(
                IncidentRecoveryNotificationRecord(
                    id=delivery_id,
                    incident_id=incident.id,
                    delivery_key=make_recovery_delivery_key(incident.id),
                    delivery_key_version=RECOVERY_DELIVERY_KEY_VERSION,
                    provider=self.notification_provider.name,
                    status=NotificationDeliveryStatus.PENDING,
                    attempt_count=0,
                    payload=payload.model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                )
            )
            return delivery_id

    def _retryable_notification_ids(self) -> list[UUID]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(IncidentRecoveryNotificationRecord.id)
                    .where(
                        IncidentRecoveryNotificationRecord.status.in_(
                            {
                                NotificationDeliveryStatus.PENDING,
                                NotificationDeliveryStatus.FAILED,
                            }
                        )
                    )
                    .order_by(
                        IncidentRecoveryNotificationRecord.updated_at,
                        IncidentRecoveryNotificationRecord.id,
                    )
                    .limit(_NOTIFICATION_RETRY_BATCH_SIZE)
                )
            )

    def _deliver_notification(self, delivery_id: UUID) -> bool:
        provider_error: NotificationProviderError | None = None
        delivered = False
        now = _utc_datetime(self.clock())
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(IncidentRecoveryNotificationRecord)
                .where(IncidentRecoveryNotificationRecord.id == delivery_id)
                .with_for_update()
            )
            if record is None:  # pragma: no cover - database invariant
                raise RecoveryCheckerError("Recovery notification disappeared")
            if record.status == NotificationDeliveryStatus.DELIVERED:
                return False
            if record.provider != self.notification_provider.name:
                raise RecoveryCheckerError("Recovery notification Provider changed")
            payload = RecoveryNotificationPayload.model_validate(record.payload)
            record.attempt_count += 1
            record.updated_at = now
            try:
                status_code = self.notification_provider.send(
                    payload,
                    delivery_key=record.delivery_key,
                )
            except NotificationProviderError as error:
                record.status = NotificationDeliveryStatus.FAILED
                record.last_error_category = error.category.value
                record.last_response_status = error.response_status
                increment_counter(
                    session,
                    "dagsentry_notification_attempts_total",
                    "failed",
                    now=now,
                )
                provider_error = error
            else:
                record.status = NotificationDeliveryStatus.DELIVERED
                record.last_error_category = None
                record.last_response_status = status_code
                record.delivered_at = now
                increment_counter(
                    session,
                    "dagsentry_notification_attempts_total",
                    "delivered",
                    now=now,
                )
                delivered = True
        if provider_error is not None:
            raise provider_error
        return delivered


def make_recovery_delivery_key(incident_id: UUID) -> str:
    """Build the stable receiver idempotency key for one recovery."""
    canonical = json.dumps(
        {
            "delivery_key_version": RECOVERY_DELIVERY_KEY_VERSION,
            "incident_id": str(incident_id),
            "notification_type": "INCIDENT_RECOVERED",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Recovery Checker clock must include a timezone")
    return value.astimezone(UTC)
