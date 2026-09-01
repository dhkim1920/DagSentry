from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

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
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.incident import correlate_failure, transition_incident
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import ErrorSignatureRecord, FailureEventRecord, IncidentRecord
from dagsentry.reporting import aggregate_daily_statistics

pytestmark = pytest.mark.integration


def test_postgres_daily_statistics_and_duration_sql() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    report_date = date(2026, 8, 12)
    start = datetime(2026, 8, 12, tzinfo=UTC)
    environment = f"report-{uuid4().hex[:12]}"
    signature_id = uuid4()
    incident_id: UUID | None = None
    with session_factory.begin() as session:
        session.add(
            ErrorSignatureRecord(
                id=signature_id,
                fingerprint_version=1,
                fingerprint=hashlib.sha256(environment.encode()).hexdigest(),
                operator_type="PythonOperator",
                exception_class="RuntimeError",
                vendor_error_code=None,
                normalized_message=environment,
                application_stack_frame=None,
                created_at=start,
            )
        )

    try:
        with session_factory() as session:
            failure = ingest_failure_event(
                session,
                FailureEventCreate(
                    environment=environment,
                    dag_id="orders",
                    dag_run_id="run",
                    task_id="load",
                    map_index=-1,
                    try_number=1,
                    source=CollectionSource.LISTENER,
                    state=FailureState.FAILED,
                    observed_at=start + timedelta(minutes=1),
                ),
            )
            persist_diagnosis(
                session,
                DiagnosisDraft(
                    failure_event_id=failure.failure_event_id,
                    error_signature_id=signature_id,
                    source=DiagnosisSource.RULE,
                    validation_status=DiagnosisValidationStatus.PASSED,
                    versions=DiagnosisVersions(1, None, 1),
                    content=DiagnosisContent(
                        classification=ErrorClassification.CONFIGURATION,
                        confidence=1,
                        evidence=(),
                        recommended_actions=(),
                        retry_decision=RetryDecision.NOT_RETRYABLE,
                    ),
                ),
            )
            incident_id = correlate_failure(
                session,
                failure_event_id=failure.failure_event_id,
                error_signature_id=signature_id,
            ).incident_id
        with session_factory.begin() as session:
            incident = session.get(IncidentRecord, incident_id)
            assert incident is not None
            incident.created_at = start
            incident.updated_at = start
        with session_factory() as session:
            transition_incident(
                session,
                incident_id=incident_id,
                target=IncidentStatus.RECOVERED,
                initiator=IncidentTransitionInitiator.SYSTEM,
                actor="integration-test",
                now=start + timedelta(hours=2),
            )
        with session_factory() as session:
            statistics = aggregate_daily_statistics(
                session,
                report_date=report_date,
                environment=environment,
            )

        assert statistics.failure_attempts == 1
        assert statistics.affected_task_instances == 1
        assert statistics.affected_dag_runs == 1
        assert statistics.incidents.new == 1
        assert statistics.incidents.recovered == 1
        assert statistics.incidents.unresolved == 0
        assert statistics.error_signatures.new == 1
        assert statistics.error_signatures.repeated == 0
        assert statistics.classification_counts[ErrorClassification.CONFIGURATION] == 1
        assert statistics.mean_time.recovery_seconds == pytest.approx(7_200)
        assert statistics.mean_time.resolution_seconds is None
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(FailureEventRecord).where(FailureEventRecord.environment == environment)
            )
            session.execute(
                delete(ErrorSignatureRecord).where(ErrorSignatureRecord.id == signature_id)
            )
