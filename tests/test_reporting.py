from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

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
from dagsentry.domain.incident import IncidentStatus, IncidentTransitionInitiator
from dagsentry.domain.reporting import STATISTICS_SCHEMA_VERSION, DailyStatistics
from dagsentry.incident import correlate_failure, transition_incident
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import DiagnosisRecord, ErrorSignatureRecord, IncidentRecord
from dagsentry.reporting import aggregate_daily_statistics

REPORT_DATE = date(2026, 8, 12)
START = datetime(2026, 8, 12, tzinfo=UTC)
END = START + timedelta(days=1)


def add_failure(
    session_factory: SessionFactory,
    *,
    observed_at: datetime,
    dag_run_id: str,
    task_id: str,
    try_number: int,
    source: CollectionSource = CollectionSource.LISTENER,
    state: FailureState = FailureState.FAILED,
) -> UUID:
    with session_factory() as session:
        return ingest_failure_event(
            session,
            FailureEventCreate(
                environment="production",
                dag_id="orders",
                dag_run_id=dag_run_id,
                task_id=task_id,
                map_index=-1,
                try_number=try_number,
                source=source,
                state=state,
                observed_at=observed_at,
            ),
        ).failure_event_id


def add_signature(session_factory: SessionFactory, name: str, *, created_at: datetime) -> UUID:
    signature_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            ErrorSignatureRecord(
                id=signature_id,
                fingerprint_version=1,
                fingerprint=hashlib.sha256(name.encode()).hexdigest(),
                operator_type="PythonOperator",
                exception_class="RuntimeError",
                vendor_error_code=None,
                normalized_message=name,
                application_stack_frame=None,
                created_at=created_at,
            )
        )
    return signature_id


def add_diagnosis(
    session_factory: SessionFactory,
    *,
    failure_event_id: UUID,
    signature_id: UUID,
    classification: ErrorClassification,
) -> UUID:
    with session_factory() as session:
        return persist_diagnosis(
            session,
            DiagnosisDraft(
                failure_event_id=failure_event_id,
                error_signature_id=signature_id,
                source=DiagnosisSource.RULE,
                validation_status=DiagnosisValidationStatus.PASSED,
                versions=DiagnosisVersions(1, None, 1),
                content=DiagnosisContent(
                    classification=classification,
                    confidence=1,
                    evidence=(),
                    recommended_actions=(),
                    retry_decision=RetryDecision.UNKNOWN,
                ),
            ),
        )


def add_reused_diagnosis(
    session_factory: SessionFactory,
    *,
    failure_event_id: UUID,
    signature_id: UUID,
    original_diagnosis_id: UUID,
) -> None:
    with session_factory.begin() as session:
        session.add(
            DiagnosisRecord(
                failure_event_id=failure_event_id,
                error_signature_id=signature_id,
                source=DiagnosisSource.REUSED,
                validation_status=DiagnosisValidationStatus.PASSED,
                classification=None,
                root_cause=None,
                confidence=None,
                confidence_reason=None,
                matched_rule=None,
                extracted_values=None,
                evidence=None,
                recommended_actions=None,
                retry_decision=None,
                operator_review_required=None,
                validation_errors=None,
                diagnosis_schema_version=1,
                prompt_version=None,
                rule_version=1,
                reused_from_diagnosis_id=original_diagnosis_id,
            )
        )


def add_incident(
    session_factory: SessionFactory,
    *,
    failure_event_id: UUID,
    signature_id: UUID,
    created_at: datetime,
) -> UUID:
    with session_factory() as session:
        incident_id = correlate_failure(
            session,
            failure_event_id=failure_event_id,
            error_signature_id=signature_id,
        ).incident_id
    with session_factory.begin() as session:
        incident = session.get(IncidentRecord, incident_id)
        assert incident is not None
        incident.created_at = created_at
        incident.updated_at = created_at
    return incident_id


def add_transition(
    session_factory: SessionFactory,
    *,
    incident_id: UUID,
    target: IncidentStatus,
    at: datetime,
) -> None:
    with session_factory() as session:
        transition_incident(
            session,
            incident_id=incident_id,
            target=target,
            initiator=(
                IncidentTransitionInitiator.SYSTEM
                if target == IncidentStatus.RECOVERED
                else IncidentTransitionInitiator.OPERATOR
            ),
            actor="report-fixture",
            now=at,
        )


def populated_statistics(session_factory: SessionFactory) -> DailyStatistics:
    repeated_signature = add_signature(
        session_factory,
        "repeated",
        created_at=START - timedelta(days=2),
    )
    prior_failure = add_failure(
        session_factory,
        observed_at=START - timedelta(days=1),
        dag_run_id="run-prior",
        task_id="retrying",
        try_number=1,
    )
    add_diagnosis(
        session_factory,
        failure_event_id=prior_failure,
        signature_id=repeated_signature,
        classification=ErrorClassification.DAG_CODE,
    )
    prior_incident = add_incident(
        session_factory,
        failure_event_id=prior_failure,
        signature_id=repeated_signature,
        created_at=START - timedelta(days=1),
    )
    add_transition(
        session_factory,
        incident_id=prior_incident,
        target=IncidentStatus.RESOLVED,
        at=START + timedelta(hours=1),
    )

    retry_failure = add_failure(
        session_factory,
        observed_at=START + timedelta(hours=2),
        dag_run_id="run-retry-success",
        task_id="retrying",
        try_number=2,
        source=CollectionSource.RETRY_CALLBACK,
        state=FailureState.UP_FOR_RETRY,
    )
    add_diagnosis(
        session_factory,
        failure_event_id=retry_failure,
        signature_id=repeated_signature,
        classification=ErrorClassification.DAG_CODE,
    )
    recovered_incident = add_incident(
        session_factory,
        failure_event_id=retry_failure,
        signature_id=repeated_signature,
        created_at=START + timedelta(hours=2),
    )
    add_transition(
        session_factory,
        incident_id=recovered_incident,
        target=IncidentStatus.RECOVERED,
        at=START + timedelta(hours=3),
    )

    new_signature = add_signature(session_factory, "new", created_at=START)
    first_failure = add_failure(
        session_factory,
        observed_at=START,
        dag_run_id="run-current",
        task_id="extract",
        try_number=1,
    )
    first_diagnosis = add_diagnosis(
        session_factory,
        failure_event_id=first_failure,
        signature_id=new_signature,
        classification=ErrorClassification.NETWORK,
    )
    unresolved_incident = add_incident(
        session_factory,
        failure_event_id=first_failure,
        signature_id=new_signature,
        created_at=START + timedelta(minutes=10),
    )
    second_failure = add_failure(
        session_factory,
        observed_at=END - timedelta(seconds=1),
        dag_run_id="run-current",
        task_id="extract",
        try_number=2,
    )
    add_reused_diagnosis(
        session_factory,
        failure_event_id=second_failure,
        signature_id=new_signature,
        original_diagnosis_id=first_diagnosis,
    )
    with session_factory() as session:
        correlated = correlate_failure(
            session,
            failure_event_id=second_failure,
            error_signature_id=new_signature,
        )
        assert correlated.incident_id == unresolved_incident
    add_transition(
        session_factory,
        incident_id=unresolved_incident,
        target=IncidentStatus.RESOLVED,
        at=END + timedelta(hours=1),
    )

    boundary_signature = add_signature(session_factory, "boundary", created_at=END)
    boundary_failure = add_failure(
        session_factory,
        observed_at=END,
        dag_run_id="run-next-day",
        task_id="boundary",
        try_number=1,
    )
    add_diagnosis(
        session_factory,
        failure_event_id=boundary_failure,
        signature_id=boundary_signature,
        classification=ErrorClassification.UNKNOWN,
    )
    add_incident(
        session_factory,
        failure_event_id=boundary_failure,
        signature_id=boundary_signature,
        created_at=END,
    )

    with session_factory() as session:
        return aggregate_daily_statistics(
            session,
            report_date=REPORT_DATE,
            environment="production",
        )


def test_daily_statistics_use_utc_boundaries_and_reconstruct_period_end_state(
    session_factory: SessionFactory,
) -> None:
    statistics = populated_statistics(session_factory)

    assert statistics.schema_version == 2
    assert statistics.timezone == "UTC"
    assert statistics.period_start == START
    assert statistics.period_end == END
    assert statistics.failure_attempts == 3
    assert statistics.affected_task_instances == 2
    assert statistics.affected_dag_runs == 2
    assert statistics.incidents.new == 2
    assert statistics.incidents.unresolved == 1
    assert statistics.incidents.recovered == 1
    assert statistics.error_signatures.new == 1
    assert statistics.error_signatures.repeated == 1
    assert statistics.classification_counts[ErrorClassification.DAG_CODE] == 1
    assert statistics.classification_counts[ErrorClassification.NETWORK] == 2
    assert sum(statistics.classification_counts.values()) == 3
    assert statistics.mean_time.recovery_seconds == pytest.approx(3_600)
    assert statistics.mean_time.resolution_seconds == pytest.approx(90_000)


def test_empty_period_has_complete_zero_statistics(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        statistics = aggregate_daily_statistics(
            session,
            report_date=REPORT_DATE,
            environment="staging",
        )

    assert statistics.failure_attempts == 0
    assert statistics.affected_task_instances == 0
    assert statistics.affected_dag_runs == 0
    assert statistics.incidents.model_dump() == {"new": 0, "unresolved": 0, "recovered": 0}
    assert statistics.error_signatures.model_dump() == {"new": 0, "repeated": 0}
    assert set(statistics.classification_counts) == set(ErrorClassification)
    assert set(statistics.classification_counts.values()) == {0}
    assert statistics.mean_time.recovery_seconds is None
    assert statistics.mean_time.resolution_seconds is None


def test_statistics_json_schema_is_versioned_and_forbids_unknown_fields() -> None:
    schema = DailyStatistics.model_json_schema()

    assert STATISTICS_SCHEMA_VERSION == 2
    assert schema["properties"]["schema_version"]["const"] == 2
    assert schema["properties"]["timezone"]["type"] == "string"
    assert schema["additionalProperties"] is False


def test_kst_boundaries_include_exact_tasks_and_keep_latest_diagnosed_failure(
    session_factory: SessionFactory,
) -> None:
    start = START - timedelta(hours=9)
    end = END - timedelta(hours=9)
    ids = {}
    for task, when in [
        ("before", start - timedelta(microseconds=1)),
        ("start", start),
        ("inside", end - timedelta(microseconds=1)),
        ("end", end),
    ]:
        ids[task] = add_failure(
            session_factory, observed_at=when, dag_run_id="boundary", task_id=task, try_number=1
        )
    signature = add_signature(session_factory, "kst", created_at=start)
    add_diagnosis(
        session_factory,
        failure_event_id=ids["start"],
        signature_id=signature,
        classification=ErrorClassification.NETWORK,
    )
    incident_id = add_incident(
        session_factory, failure_event_id=ids["start"], signature_id=signature, created_at=start
    )
    add_failure(
        session_factory,
        observed_at=end - timedelta(seconds=1),
        dag_run_id="pending",
        task_id="start",
        try_number=2,
    )
    with session_factory() as session:
        statistics = aggregate_daily_statistics(
            session, report_date=REPORT_DATE, environment="production", timezone="Asia/Seoul"
        )
    assert statistics.period_start == start
    assert statistics.period_end == end
    assert statistics.failure_attempts == 3
    assert [item.task_id for item in statistics.top_failures] == ["start", "inside"]
    first = statistics.top_failures[0]
    assert first.failure_count == 2
    assert first.last_failed_at == end - timedelta(seconds=1)
    assert first.classification == ErrorClassification.NETWORK
    assert first.incident_id == incident_id


def test_local_day_across_dst_and_invalid_timezone() -> None:
    from dagsentry.domain.reporting import report_period

    start, end = report_period(date(2026, 3, 8), "America/New_York")
    assert end - start == timedelta(hours=23)
    with pytest.raises(ValueError, match="IANA"):
        report_period(REPORT_DATE, "Not/A_Timezone")


def test_top_failures_limit_and_stable_ties(session_factory: SessionFactory) -> None:
    for index in range(22):
        add_failure(
            session_factory,
            observed_at=START,
            dag_run_id="top",
            task_id=f"task_{index:02}",
            try_number=1,
        )
    with session_factory() as session:
        statistics = aggregate_daily_statistics(
            session, report_date=REPORT_DATE, environment="production"
        )
    assert statistics.failure_attempts == 22
    assert [item.task_id for item in statistics.top_failures] == [
        f"task_{index:02}" for index in range(20)
    ]
