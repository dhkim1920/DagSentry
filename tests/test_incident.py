from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.domain.failure_event import CollectionSource, FailureEventCreate, FailureState
from dagsentry.domain.incident import (
    IncidentStatus,
    IncidentTransitionInitiator,
    validate_incident_transition,
)
from dagsentry.error_signature import (
    ErrorSignatureInput,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.incident import correlate_failure, transition_incident
from dagsentry.ingestion import ingest_failure_event
from dagsentry.models import IncidentFailureRecord, IncidentRecord

NOW = datetime(2026, 8, 11, tzinfo=UTC)


def create_failure(
    session: Session,
    *,
    try_number: int,
    environment: str = "production",
    dag_id: str = "orders",
    task_id: str = "load",
) -> UUID:
    return ingest_failure_event(
        session,
        FailureEventCreate(
            environment=environment,
            dag_id=dag_id,
            dag_run_id="scheduled__2026-08-11",
            task_id=task_id,
            map_index=-1,
            try_number=try_number,
            source=CollectionSource.LISTENER,
            state=FailureState.FAILED,
            observed_at=NOW,
        ),
    ).failure_event_id


def create_signature(session: Session, message: str = "ValueError: invalid order") -> UUID:
    result = persist_error_signature(
        session,
        build_error_signature(
            ErrorSignatureInput(
                operator_type="PythonOperator",
                exception_class="ValueError",
                vendor_error_code=None,
                normalized_message=message,
                application_stack_frame=None,
            )
        ),
    )
    assert result.signature_id is not None
    return result.signature_id


def test_equivalent_active_failures_share_one_incident(session: Session) -> None:
    first_failure = create_failure(session, try_number=1)
    second_failure = create_failure(session, try_number=2)
    signature_id = create_signature(session)

    first = correlate_failure(
        session,
        failure_event_id=first_failure,
        error_signature_id=signature_id,
    )
    second = correlate_failure(
        session,
        failure_event_id=second_failure,
        error_signature_id=signature_id,
    )
    repeated = correlate_failure(
        session,
        failure_event_id=second_failure,
        error_signature_id=signature_id,
    )

    assert first.incident_created is True
    assert first.is_initial_failure is True
    assert second.incident_created is False
    assert second.is_initial_failure is False
    assert second.incident_id == first.incident_id
    assert repeated.incident_id == first.incident_id
    assert repeated.failure_link_created is False
    assert repeated.is_initial_failure is False
    assert session.scalar(select(func.count()).select_from(IncidentRecord)) == 1
    assert session.scalar(select(func.count()).select_from(IncidentFailureRecord)) == 2


def test_correlation_identity_includes_environment_dag_task_and_signature(
    session: Session,
) -> None:
    signature_id = create_signature(session)
    failures = (
        create_failure(session, try_number=1),
        create_failure(session, try_number=1, environment="staging"),
        create_failure(session, try_number=1, dag_id="customers"),
        create_failure(session, try_number=1, task_id="validate"),
    )
    second_signature = create_signature(session, "ValueError: missing customer")

    incident_ids = {
        correlate_failure(
            session,
            failure_event_id=failure_id,
            error_signature_id=signature_id,
        ).incident_id
        for failure_id in failures
    }
    other_signature_failure = create_failure(session, try_number=2)
    incident_ids.add(
        correlate_failure(
            session,
            failure_event_id=other_signature_failure,
            error_signature_id=second_signature,
        ).incident_id
    )

    assert len(incident_ids) == 5


def test_terminal_incident_is_not_reopened_by_a_new_failure(session: Session) -> None:
    first_failure = create_failure(session, try_number=1)
    second_failure = create_failure(session, try_number=2)
    signature_id = create_signature(session)
    first = correlate_failure(
        session,
        failure_event_id=first_failure,
        error_signature_id=signature_id,
    )

    transition_incident(
        session,
        incident_id=first.incident_id,
        target=IncidentStatus.RESOLVED,
        initiator=IncidentTransitionInitiator.OPERATOR,
        actor="oncall@example.com",
        now=NOW,
    )
    second = correlate_failure(
        session,
        failure_event_id=second_failure,
        error_signature_id=signature_id,
    )

    assert second.incident_created is True
    assert second.incident_id != first.incident_id


def test_unsignable_failures_are_never_grouped_together(session: Session) -> None:
    first_failure = create_failure(session, try_number=1)
    second_failure = create_failure(session, try_number=2)

    first = correlate_failure(
        session,
        failure_event_id=first_failure,
        error_signature_id=None,
    )
    second = correlate_failure(
        session,
        failure_event_id=second_failure,
        error_signature_id=None,
    )
    repeated = correlate_failure(
        session,
        failure_event_id=first_failure,
        error_signature_id=None,
    )

    assert first.incident_id != second.incident_id
    assert repeated.incident_id == first.incident_id
    assert repeated.failure_link_created is False
    assert repeated.is_initial_failure is True


@pytest.mark.parametrize(
    ("current", "target", "initiator"),
    [
        (
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentTransitionInitiator.OPERATOR,
        ),
        (
            IncidentStatus.OPEN,
            IncidentStatus.RECOVERED,
            IncidentTransitionInitiator.SYSTEM,
        ),
        (
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.RESOLVED,
            IncidentTransitionInitiator.OPERATOR,
        ),
        (
            IncidentStatus.RECOVERED,
            IncidentStatus.IGNORED,
            IncidentTransitionInitiator.OPERATOR,
        ),
    ],
)
def test_allowed_incident_transitions(
    current: IncidentStatus,
    target: IncidentStatus,
    initiator: IncidentTransitionInitiator,
) -> None:
    validate_incident_transition(current, target, initiator)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IncidentStatus.RESOLVED, IncidentStatus.OPEN),
        (IncidentStatus.RESOLVED, IncidentStatus.ACKNOWLEDGED),
        (IncidentStatus.RESOLVED, IncidentStatus.IGNORED),
        (IncidentStatus.IGNORED, IncidentStatus.OPEN),
        (IncidentStatus.IGNORED, IncidentStatus.ACKNOWLEDGED),
        (IncidentStatus.IGNORED, IncidentStatus.RESOLVED),
    ],
)
def test_authorized_operator_can_override_a_terminal_incident(
    current: IncidentStatus,
    target: IncidentStatus,
) -> None:
    validate_incident_transition(
        current,
        target,
        IncidentTransitionInitiator.OPERATOR,
        allow_terminal_override=True,
    )


@pytest.mark.parametrize(
    ("current", "target", "initiator"),
    [
        (
            IncidentStatus.RESOLVED,
            IncidentStatus.OPEN,
            IncidentTransitionInitiator.OPERATOR,
        ),
        (
            IncidentStatus.IGNORED,
            IncidentStatus.ACKNOWLEDGED,
            IncidentTransitionInitiator.OPERATOR,
        ),
        (
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentTransitionInitiator.SYSTEM,
        ),
        (
            IncidentStatus.OPEN,
            IncidentStatus.RECOVERED,
            IncidentTransitionInitiator.AI,
        ),
        (
            IncidentStatus.OPEN,
            IncidentStatus.RESOLVED,
            IncidentTransitionInitiator.AI,
        ),
    ],
)
def test_invalid_or_ai_incident_transitions_are_rejected(
    current: IncidentStatus,
    target: IncidentStatus,
    initiator: IncidentTransitionInitiator,
) -> None:
    with pytest.raises(ValueError):
        validate_incident_transition(current, target, initiator)
