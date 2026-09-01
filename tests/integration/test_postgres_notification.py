from __future__ import annotations

import os
from _thread import LockType
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from dagsentry.db import create_session_factory
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
from dagsentry.domain.notification import NotificationPayload
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import FailureEventRecord, NotificationDeliveryRecord
from dagsentry.notification import NotificationDeliveryResult, deliver_notification

pytestmark = pytest.mark.integration


@dataclass
class CountingProvider:
    name: str = "integration-stub"
    calls: int = 0
    lock: LockType = field(default_factory=Lock)

    def send(self, _payload: NotificationPayload, *, delivery_key: str) -> int:
        assert delivery_key
        with self.lock:
            self.calls += 1
        return 200


def test_concurrent_notification_delivery_calls_provider_once() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    with session_factory() as session:
        failure = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="integration",
                dag_id="notification",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
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
    payload = _payload(failure.failure_event_id, diagnosis_id)
    provider = CountingProvider()
    barrier = Barrier(8)

    def deliver() -> NotificationDeliveryResult:
        barrier.wait()
        return deliver_notification(session_factory, provider=provider, payload=payload)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: deliver(), range(8)))

        assert provider.calls == 1
        assert sum(not result.already_delivered for result in results) == 1
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )


def _payload(failure_id: UUID, diagnosis_id: UUID) -> NotificationPayload:
    return NotificationPayload(
        failure_event_id=failure_id,
        diagnosis_id=diagnosis_id,
        incident_id=failure_id,
        incident_status=IncidentStatus.OPEN,
        incident_failure_count=1,
        environment="integration",
        dag_id="notification",
        dag_run_id="run",
        task_id="load",
        map_index=-1,
        try_number=1,
        failed_at=datetime.now(UTC),
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
