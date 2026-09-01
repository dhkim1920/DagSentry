"""Recovery Checker contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dagsentry.domain.incident import IncidentStatus


class RecoveryNotificationPayload(BaseModel):
    """Provider-neutral notification emitted after automatic recovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: UUID
    incident_status: Literal[IncidentStatus.RECOVERED] = IncidentStatus.RECOVERED
    incident_failure_count: int = Field(ge=1)
    environment: str
    dag_id: str
    task_id: str
    recovered_at: datetime


class RecoveryNotificationProvider(Protocol):
    """Notification boundary used by the Recovery Checker."""

    @property
    def name(self) -> str:
        """Stable Provider name stored with delivery attempts."""

    def send(self, payload: RecoveryNotificationPayload, *, delivery_key: str) -> int:
        """Deliver one recovery payload using a stable idempotency key."""
