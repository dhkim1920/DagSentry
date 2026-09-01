from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from dagsentry.db import SessionFactory
from dagsentry.diagnosis import DiagnosisDraft, persist_diagnosis
from dagsentry.domain.diagnosis import (
    DiagnosisContent,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
    NotificationSuppressionReason,
)
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import (
    DiagnosisRecord,
    NotificationDeliveryRecord,
    OperationalMetricCounterRecord,
)
from dagsentry.notification import deliver_notification, make_delivery_key, suppress_notification


def stored_diagnosis(session_factory: SessionFactory) -> tuple[UUID, UUID]:
    with session_factory() as session:
        failure = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="test",
                dag_id="orders",
                dag_run_id="run",
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime(2026, 8, 10, tzinfo=UTC),
            ),
        )
        diagnosis_id = persist_diagnosis(
            session,
            DiagnosisDraft(
                failure_event_id=failure.failure_event_id,
                error_signature_id=None,
                source=DiagnosisSource.RULE,
                validation_status=DiagnosisValidationStatus.PASSED,
                versions=DiagnosisVersions(1, None, 1),
                content=DiagnosisContent(
                    classification=ErrorClassification.UNKNOWN,
                    confidence=0,
                    evidence=(),
                    recommended_actions=(),
                    retry_decision=RetryDecision.UNKNOWN,
                ),
            ),
        )
    return failure.failure_event_id, diagnosis_id


def notification_payload(failure_id: UUID, diagnosis_id: UUID) -> NotificationPayload:
    return NotificationPayload(
        failure_event_id=failure_id,
        diagnosis_id=diagnosis_id,
        incident_id=failure_id,
        incident_status=IncidentStatus.OPEN,
        incident_failure_count=1,
        environment="test",
        dag_id="orders",
        dag_run_id="run",
        task_id="load",
        map_index=-1,
        try_number=1,
        failed_at=datetime(2026, 8, 10, tzinfo=UTC),
        classification=ErrorClassification.UNKNOWN,
        root_cause=None,
        confidence=0,
        evidence=[],
        error_signature=None,
        recommended_actions=[],
        retry_decision=RetryDecision.UNKNOWN,
        airflow_log_url=None,
        diagnosis_source=DiagnosisSource.RULE,
        is_rule_fallback=True,
    )


@dataclass
class StubProvider:
    name: str = "stub"
    calls: int = 0
    fail: bool = False
    keys: list[str] | None = None

    def send(self, _payload: NotificationPayload, *, delivery_key: str) -> int:
        self.calls += 1
        if self.keys is not None:
            self.keys.append(delivery_key)
        if self.fail:
            raise NotificationProviderError(
                "temporary failure",
                category=NotificationErrorCategory.UNAVAILABLE,
                retryable=True,
                response_status=503,
            )
        return 200


def test_delivery_is_idempotent_and_snapshot_is_persisted(
    session_factory: SessionFactory,
) -> None:
    failure_id, diagnosis_id = stored_diagnosis(session_factory)
    payload = notification_payload(failure_id, diagnosis_id)
    keys: list[str] = []
    provider = StubProvider(keys=keys)

    first = deliver_notification(session_factory, provider=provider, payload=payload)
    duplicate = deliver_notification(session_factory, provider=provider, payload=payload)

    assert first.delivered is True
    assert first.already_delivered is False
    assert duplicate.already_delivered is True
    assert provider.calls == 1
    assert keys == [make_delivery_key(diagnosis_id)]
    with session_factory() as session:
        record = session.scalar(select(NotificationDeliveryRecord))
        assert record is not None
        assert record.status == NotificationDeliveryStatus.DELIVERED
        assert record.attempt_count == 1
        assert record.payload == payload.model_dump(mode="json")
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1


def test_failed_notification_does_not_rollback_diagnosis_and_can_retry(
    session_factory: SessionFactory,
) -> None:
    failure_id, diagnosis_id = stored_diagnosis(session_factory)
    payload = notification_payload(failure_id, diagnosis_id)
    provider = StubProvider(fail=True)

    with pytest.raises(NotificationProviderError):
        deliver_notification(session_factory, provider=provider, payload=payload)

    with session_factory() as session:
        assert session.get(DiagnosisRecord, diagnosis_id) is not None
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.FAILED
        assert delivery.attempt_count == 1
        assert delivery.last_error_category == NotificationErrorCategory.UNAVAILABLE
        assert delivery.last_response_status == 503
        failed_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_notification_attempts_total", "failed"),
        )
        assert failed_metric is not None and failed_metric.value == 1

    provider.fail = False
    result = deliver_notification(session_factory, provider=provider, payload=payload)

    assert result.delivered is True
    assert provider.calls == 2
    with session_factory() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.DELIVERED
        assert delivery.attempt_count == 2
        delivered_metric = session.get(
            OperationalMetricCounterRecord,
            ("dagsentry_notification_attempts_total", "delivered"),
        )
        assert delivered_metric is not None and delivered_metric.value == 1


def test_suppression_is_persisted_and_idempotent_without_provider_call(
    session_factory: SessionFactory,
) -> None:
    failure_id, diagnosis_id = stored_diagnosis(session_factory)
    payload = notification_payload(failure_id, diagnosis_id)

    first = suppress_notification(
        session_factory,
        provider_name="stub",
        payload=payload,
        reason=NotificationSuppressionReason.REPEATED_ACTIVE_INCIDENT,
    )
    repeated = suppress_notification(
        session_factory,
        provider_name="stub",
        payload=payload,
        reason=NotificationSuppressionReason.REPEATED_ACTIVE_INCIDENT,
    )

    assert first.suppressed is True
    assert repeated.suppressed is True
    assert repeated.delivery_id == first.delivery_id
    with session_factory() as session:
        record = session.get(NotificationDeliveryRecord, first.delivery_id)
        assert record is not None
        assert record.status == NotificationDeliveryStatus.SUPPRESSED
        assert record.suppression_reason == "REPEATED_ACTIVE_INCIDENT"
        assert record.attempt_count == 0
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1
