"""Persistent, idempotent Notification delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from dagsentry.db import SessionFactory
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationPayload,
    NotificationProvider,
    NotificationProviderError,
    NotificationSuppressionReason,
)
from dagsentry.metrics import increment_counter
from dagsentry.models import NotificationDeliveryRecord

DELIVERY_KEY_VERSION = 1


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """Outcome of an idempotent delivery request."""

    delivery_id: UUID
    delivery_key: str
    delivered: bool
    already_delivered: bool
    suppressed: bool


def make_delivery_key(diagnosis_id: UUID) -> str:
    """Build the stable key receivers can use for their own idempotency."""
    canonical = json.dumps(
        {
            "delivery_key_version": DELIVERY_KEY_VERSION,
            "diagnosis_id": str(diagnosis_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def deliver_notification(
    session_factory: SessionFactory,
    *,
    provider: NotificationProvider,
    payload: NotificationPayload,
) -> NotificationDeliveryResult:
    """Deliver once per Diagnosis while preserving every attempt in the database."""
    delivery_key = make_delivery_key(payload.diagnosis_id)
    delivery_id, _ = _ensure_delivery(
        session_factory,
        provider=provider,
        payload=payload,
        delivery_key=delivery_key,
        initial_status=NotificationDeliveryStatus.PENDING,
        suppression_reason=None,
    )
    provider_error: NotificationProviderError | None = None

    with session_factory.begin() as session:
        record = session.scalar(
            select(NotificationDeliveryRecord)
            .where(NotificationDeliveryRecord.id == delivery_id)
            .with_for_update()
        )
        if record is None:  # pragma: no cover - database invariant
            raise RuntimeError("Notification delivery disappeared")
        if record.status == NotificationDeliveryStatus.DELIVERED:
            return NotificationDeliveryResult(
                delivery_id=record.id,
                delivery_key=record.delivery_key,
                delivered=True,
                already_delivered=True,
                suppressed=False,
            )
        if record.status == NotificationDeliveryStatus.SUPPRESSED:
            raise RuntimeError("Suppressed Notification cannot be delivered")

        record.attempt_count += 1
        record.updated_at = datetime.now(UTC)
        try:
            status_code = provider.send(payload, delivery_key=record.delivery_key)
        except NotificationProviderError as error:
            record.status = NotificationDeliveryStatus.FAILED
            record.last_error_category = error.category.value
            record.last_response_status = error.response_status
            increment_counter(
                session,
                "dagsentry_notification_attempts_total",
                "failed",
            )
            provider_error = error
        else:
            record.status = NotificationDeliveryStatus.DELIVERED
            record.last_error_category = None
            record.last_response_status = status_code
            record.delivered_at = datetime.now(UTC)
            increment_counter(
                session,
                "dagsentry_notification_attempts_total",
                "delivered",
            )

    if provider_error is not None:
        raise provider_error
    return NotificationDeliveryResult(
        delivery_id=delivery_id,
        delivery_key=delivery_key,
        delivered=True,
        already_delivered=False,
        suppressed=False,
    )


def suppress_notification(
    session_factory: SessionFactory,
    *,
    provider_name: str,
    payload: NotificationPayload,
    reason: NotificationSuppressionReason,
) -> NotificationDeliveryResult:
    """Persist an idempotent policy decision without calling a Provider."""
    delivery_key = make_delivery_key(payload.diagnosis_id)
    delivery_id, _ = _ensure_delivery(
        session_factory,
        provider_name=provider_name,
        payload=payload,
        delivery_key=delivery_key,
        initial_status=NotificationDeliveryStatus.SUPPRESSED,
        suppression_reason=reason,
    )
    with session_factory() as session:
        record = session.get(NotificationDeliveryRecord, delivery_id)
        if record is None:  # pragma: no cover - database invariant
            raise RuntimeError("Suppressed Notification disappeared")
        if record.status != NotificationDeliveryStatus.SUPPRESSED:
            raise RuntimeError("Notification suppression conflicts with existing delivery")
    return NotificationDeliveryResult(
        delivery_id=delivery_id,
        delivery_key=delivery_key,
        delivered=False,
        already_delivered=False,
        suppressed=True,
    )


def _ensure_delivery(
    session_factory: SessionFactory,
    *,
    provider: NotificationProvider | None = None,
    provider_name: str | None = None,
    payload: NotificationPayload,
    delivery_key: str,
    initial_status: NotificationDeliveryStatus,
    suppression_reason: NotificationSuppressionReason | None,
) -> tuple[UUID, bool]:
    resolved_provider_name = provider.name if provider is not None else provider_name
    if resolved_provider_name is None:  # pragma: no cover - internal contract
        raise ValueError("provider or provider_name is required")
    delivery_id = uuid4()
    values = {
        "id": delivery_id,
        "diagnosis_id": payload.diagnosis_id,
        "delivery_key": delivery_key,
        "delivery_key_version": DELIVERY_KEY_VERSION,
        "provider": resolved_provider_name,
        "status": initial_status,
        "attempt_count": 0,
        "payload": payload.model_dump(mode="json"),
        "suppression_reason": suppression_reason.value if suppression_reason is not None else None,
    }
    with session_factory.begin() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = (
                postgresql_insert(NotificationDeliveryRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["diagnosis_id"])
                .returning(NotificationDeliveryRecord.id)
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(NotificationDeliveryRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["diagnosis_id"])
                .returning(NotificationDeliveryRecord.id)
            )
        else:  # pragma: no cover
            raise RuntimeError(f"Unsupported database dialect: {dialect}")
        inserted_id = session.scalar(statement)
        if inserted_id is not None:
            return inserted_id, True
        existing_id = session.scalar(
            select(NotificationDeliveryRecord.id).where(
                NotificationDeliveryRecord.diagnosis_id == payload.diagnosis_id
            )
        )
        if existing_id is None:  # pragma: no cover
            raise RuntimeError("Conflicting Notification delivery was not found")
        return existing_id, False
