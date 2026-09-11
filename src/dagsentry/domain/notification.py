"""Provider-neutral Notification contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dagsentry.domain.diagnosis import DiagnosisSource, ErrorClassification, RetryDecision
from dagsentry.domain.failure_event import FailureState
from dagsentry.domain.incident import IncidentStatus


class NotificationDeliveryStatus(StrEnum):
    """Persisted lifecycle of one final Diagnosis delivery."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class NotificationSuppressionReason(StrEnum):
    """Stable reason a valid Diagnosis intentionally produced no remote call."""

    REPEATED_ACTIVE_INCIDENT = "REPEATED_ACTIVE_INCIDENT"
    REPEATED_FINAL_FAILURE = "REPEATED_FINAL_FAILURE"


class NotificationErrorCategory(StrEnum):
    """Stable external delivery failure categories."""

    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"


class NotificationEvidence(BaseModel):
    """One sanitized log line included in an operator notification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: int = Field(gt=0)
    text: str = Field(min_length=1)


class NotificationPayload(BaseModel):
    """Stable v0.1 payload independent of messaging vendors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_event_id: UUID
    diagnosis_id: UUID
    incident_id: UUID
    incident_status: IncidentStatus
    incident_failure_count: int = Field(ge=1)
    environment: str
    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    failed_at: datetime
    classification: ErrorClassification
    root_cause: str | None
    confidence: float = Field(ge=0, le=1)
    evidence: list[NotificationEvidence]
    error_signature: str | None
    recommended_actions: list[str]
    retry_decision: RetryDecision
    airflow_log_url: str | None
    diagnosis_source: DiagnosisSource
    is_rule_fallback: bool
    failure_state: FailureState = FailureState.FAILED


class NotificationProviderError(RuntimeError):
    """A sanitized delivery failure with a stable retry decision."""

    def __init__(
        self,
        message: str,
        *,
        category: NotificationErrorCategory,
        retryable: bool,
        response_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.response_status = response_status


class NotificationProvider(Protocol):
    """Minimal delivery boundary consumed by the Diagnosis pipeline."""

    @property
    def name(self) -> str:
        """Stable Provider name stored with delivery attempts."""

    def send(self, payload: NotificationPayload, *, delivery_key: str) -> int:
        """Deliver one payload and return the successful HTTP status code."""
