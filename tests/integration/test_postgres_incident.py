from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from dagsentry.db import create_session_factory
from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.error_signature import (
    ErrorSignatureInput,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.incident import (
    IncidentCorrelationResult,
    IncidentStateConflictError,
    correlate_failure,
    transition_incident,
)
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import (
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
    IncidentRecoveryNotificationRecord,
    IncidentStateTransitionRecord,
)
from dagsentry.recovery import RecoveryChecker, TaskInstanceReference

pytestmark = pytest.mark.integration


def test_concurrent_failures_create_one_active_incident() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    failure_ids: list[UUID] = []
    signature_id: UUID | None = None
    with session_factory() as session:
        for try_number in range(1, 9):
            failure_ids.append(
                ingest_failure_event(
                    session,
                    FailureEventCreate(
                        environment="integration",
                        dag_id="incident_concurrency",
                        dag_run_id=marker,
                        task_id="load",
                        map_index=-1,
                        try_number=try_number,
                        source=CollectionSource.LISTENER,
                        state=FailureState.FAILED,
                        observed_at=datetime.now(UTC),
                    ),
                ).failure_event_id
            )
        signature = persist_error_signature(
            session,
            build_error_signature(
                ErrorSignatureInput(
                    operator_type="PythonOperator",
                    exception_class="ValueError",
                    vendor_error_code=None,
                    normalized_message=f"ValueError: {marker}",
                    application_stack_frame=None,
                )
            ),
        )
        assert signature.signature_id is not None
        signature_id = signature.signature_id
    barrier = Barrier(8)

    def correlate(failure_id: UUID) -> IncidentCorrelationResult:
        with session_factory() as session:
            barrier.wait()
            return correlate_failure(
                session,
                failure_event_id=failure_id,
                error_signature_id=signature_id,
            )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(correlate, failure_ids))

        assert len({result.incident_id for result in results}) == 1
        assert sum(result.incident_created for result in results) == 1
        assert sum(result.is_initial_failure for result in results) == 1
        with session_factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IncidentRecord)
                    .where(IncidentRecord.error_signature_id == signature_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IncidentFailureRecord)
                    .where(IncidentFailureRecord.failure_event_id.in_(failure_ids))
                )
                == 8
            )
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )
            if signature_id is not None:
                session.execute(
                    delete(IncidentRecord).where(IncidentRecord.error_signature_id == signature_id)
                )
                session.execute(
                    delete(ErrorSignatureRecord).where(ErrorSignatureRecord.id == signature_id)
                )


def test_concurrent_operator_updates_do_not_overwrite_each_other() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    marker = uuid4().hex
    signature_id: UUID | None = None
    incident_id: UUID | None = None
    with session_factory() as session:
        failure = ingest_failure_event(
            session,
            FailureEventCreate(
                environment="integration",
                dag_id="incident_transition_concurrency",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
            ),
        )
        signature = persist_error_signature(
            session,
            build_error_signature(
                ErrorSignatureInput(
                    operator_type="PythonOperator",
                    exception_class="ValueError",
                    vendor_error_code=None,
                    normalized_message=f"ValueError: {marker}",
                    application_stack_frame=None,
                )
            ),
        )
        assert signature.signature_id is not None
        signature_id = signature.signature_id
        incident_id = correlate_failure(
            session,
            failure_event_id=failure.failure_event_id,
            error_signature_id=signature_id,
        ).incident_id
    assert incident_id is not None
    barrier = Barrier(2)

    def update(target: IncidentStatus) -> str:
        with session_factory() as session:
            barrier.wait()
            try:
                result = transition_incident(
                    session,
                    incident_id=incident_id,
                    target=target,
                    initiator=IncidentTransitionInitiator.OPERATOR,
                    actor=f"operator-{target.value.lower()}",
                    expected_status=IncidentStatus.OPEN,
                )
            except IncidentStateConflictError:
                return "CONFLICT"
            return result.status.value

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    update,
                    (IncidentStatus.ACKNOWLEDGED, IncidentStatus.IGNORED),
                )
            )

        assert results.count("CONFLICT") == 1
        with session_factory() as session:
            incident = session.get(IncidentRecord, incident_id)
            assert incident is not None
            assert incident.status.value in results
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IncidentStateTransitionRecord)
                    .where(IncidentStateTransitionRecord.incident_id == incident_id)
                )
                == 1
            )
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )
            if incident_id is not None:
                session.execute(delete(IncidentRecord).where(IncidentRecord.id == incident_id))
            if signature_id is not None:
                session.execute(
                    delete(ErrorSignatureRecord).where(ErrorSignatureRecord.id == signature_id)
                )


def test_concurrent_recovery_checks_create_one_transition_and_notification() -> None:
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
                dag_id="recovery_concurrency",
                dag_run_id=marker,
                task_id="load",
                map_index=-1,
                try_number=1,
                source=CollectionSource.LISTENER,
                state=FailureState.FAILED,
                observed_at=datetime.now(UTC),
            ),
        )
    incident_id = uuid4()
    try:
        with session_factory.begin() as session:
            session.add(
                IncidentRecord(
                    id=incident_id,
                    environment="integration",
                    dag_id="recovery_concurrency",
                    task_id="load",
                    error_signature_id=None,
                    initial_failure_event_id=failure.failure_event_id,
                    status=IncidentStatus.OPEN,
                )
            )
            session.flush()
            session.add(
                IncidentFailureRecord(
                    incident_id=incident_id,
                    failure_event_id=failure.failure_event_id,
                )
            )
    except Exception:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )
        raise

    barrier = Barrier(2)

    class SuccessfulStateClient:
        def get_state(self, _reference: TaskInstanceReference) -> str:
            barrier.wait()
            return "SUCCESS"

    class RecordingProvider:
        name = "integration"

        def __init__(self) -> None:
            self.keys: list[str] = []

        def send(
            self,
            _payload: RecoveryNotificationPayload,
            *,
            delivery_key: str,
        ) -> int:
            self.keys.append(delivery_key)
            return 200

    provider = RecordingProvider()

    def check() -> int:
        return (
            RecoveryChecker(
                session_factory,
                airflow_client=SuccessfulStateClient(),
                notification_provider=provider,
            )
            .run_once()
            .incidents_recovered
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: check(), range(2)))

        assert sum(results) == 1
        assert len(provider.keys) == 1
        with session_factory() as session:
            incident = session.get(IncidentRecord, incident_id)
            assert incident is not None
            assert incident.status == IncidentStatus.RECOVERED
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IncidentStateTransitionRecord)
                    .where(IncidentStateTransitionRecord.incident_id == incident_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IncidentRecoveryNotificationRecord)
                    .where(IncidentRecoveryNotificationRecord.incident_id == incident_id)
                )
                == 1
            )
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.dag_run_id == marker)
            )
