"""Incident correlation and lifecycle persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from dagsentry.domain.failure_event import FailureState
from dagsentry.domain.incident import (
    ACTIVE_INCIDENT_STATUSES,
    TERMINAL_INCIDENT_STATUSES,
    IncidentStatus,
    IncidentTransitionInitiator,
    validate_incident_transition,
)
from dagsentry.metrics import increment_counter
from dagsentry.models import (
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
    IncidentStateTransitionRecord,
)

_ACTIVE_INCIDENT_PREDICATE = "error_signature_id IS NOT NULL AND status IN ('OPEN', 'ACKNOWLEDGED')"


@dataclass(frozen=True)
class IncidentCorrelationResult:
    """Outcome of idempotently correlating one Failure Event."""

    incident_id: UUID
    incident_created: bool
    failure_link_created: bool
    is_initial_failure: bool
    is_final_failure: bool = False


@dataclass(frozen=True)
class IncidentTransitionResult:
    """Outcome of one validated Incident state change."""

    incident_id: UUID
    previous_status: IncidentStatus
    status: IncidentStatus
    changed: bool
    transition_id: UUID | None


class IncidentNotFoundError(ValueError):
    """The requested Incident does not exist."""


class IncidentStateConflictError(ValueError):
    """The Incident changed since the caller read it."""


class IncidentTransitionPermissionError(ValueError):
    """The actor may not override the current terminal state."""


def correlate_failure(
    session: Session,
    *,
    failure_event_id: UUID,
    error_signature_id: UUID | None,
) -> IncidentCorrelationResult:
    """Attach a failure to its active Incident, or create a new Incident."""
    with session.begin():
        failure = session.scalar(
            select(FailureEventRecord)
            .where(FailureEventRecord.id == failure_event_id)
            .with_for_update()
        )
        if failure is None:
            raise ValueError(f"Failure Event {failure_event_id} was not found")

        existing_link = session.scalar(
            select(IncidentFailureRecord).where(
                IncidentFailureRecord.failure_event_id == failure_event_id
            )
        )
        if existing_link is not None:
            incident = session.get(IncidentRecord, existing_link.incident_id)
            if incident is None:  # pragma: no cover - database invariant
                raise RuntimeError("Linked Incident was not found")
            return IncidentCorrelationResult(
                incident_id=existing_link.incident_id,
                incident_created=False,
                failure_link_created=False,
                is_initial_failure=incident.initial_failure_event_id == failure_event_id,
                is_final_failure=_claim_final_failure(session, incident.id, failure),
            )

        incident_id, incident_created = _find_or_create_incident(
            session,
            failure=failure,
            error_signature_id=error_signature_id,
        )
        if incident_created:
            increment_counter(
                session,
                "dagsentry_incident_events_total",
                "opened",
            )
        session.add(
            IncidentFailureRecord(
                incident_id=incident_id,
                failure_event_id=failure_event_id,
            )
        )
        session.flush()
        return IncidentCorrelationResult(
            incident_id=incident_id,
            incident_created=incident_created,
            failure_link_created=True,
            is_initial_failure=incident_created,
            is_final_failure=_claim_final_failure(session, incident_id, failure),
        )


def _claim_final_failure(session: Session, incident_id: UUID, failure: FailureEventRecord) -> bool:
    if failure.state != FailureState.FAILED:
        return False
    session.execute(
        update(IncidentRecord)
        .where(
            IncidentRecord.id == incident_id,
            IncidentRecord.final_failure_event_id.is_(None),
            IncidentRecord.status.in_(ACTIVE_INCIDENT_STATUSES),
        )
        .values(final_failure_event_id=failure.id)
    )
    return (
        session.scalar(
            select(IncidentRecord.final_failure_event_id).where(IncidentRecord.id == incident_id)
        )
        == failure.id
    )


def transition_incident(
    session: Session,
    *,
    incident_id: UUID,
    target: IncidentStatus,
    initiator: IncidentTransitionInitiator,
    actor: str,
    reason: str | None = None,
    expected_status: IncidentStatus | None = None,
    allow_terminal_override: bool = False,
    now: datetime | None = None,
) -> IncidentTransitionResult:
    """Apply one locked and domain-validated Incident transition."""
    with session.begin():
        incident = session.scalar(
            select(IncidentRecord).where(IncidentRecord.id == incident_id).with_for_update()
        )
        if incident is None:
            raise IncidentNotFoundError(f"Incident {incident_id} was not found")
        previous = incident.status
        if expected_status is not None and previous != expected_status:
            raise IncidentStateConflictError(
                f"Incident status is {previous}; expected {expected_status}"
            )
        terminal_override = previous in TERMINAL_INCIDENT_STATUSES and previous != target
        if terminal_override and not allow_terminal_override:
            latest_transition = session.scalar(
                select(IncidentStateTransitionRecord)
                .where(
                    IncidentStateTransitionRecord.incident_id == incident_id,
                    IncidentStateTransitionRecord.status == previous,
                )
                .order_by(
                    IncidentStateTransitionRecord.created_at.desc(),
                    IncidentStateTransitionRecord.id.desc(),
                )
                .limit(1)
            )
            if latest_transition is None or latest_transition.actor != actor:
                raise IncidentTransitionPermissionError(
                    "Only the operator who made the terminal change or an Admin may change it"
                )
        validate_incident_transition(
            previous,
            target,
            initiator,
            allow_terminal_override=terminal_override,
        )
        if (
            terminal_override
            and target in ACTIVE_INCIDENT_STATUSES
            and incident.error_signature_id is not None
        ):
            active_incident_id = session.scalar(
                select(IncidentRecord.id).where(
                    IncidentRecord.id != incident.id,
                    IncidentRecord.environment == incident.environment,
                    IncidentRecord.dag_id == incident.dag_id,
                    IncidentRecord.task_id == incident.task_id,
                    IncidentRecord.error_signature_id == incident.error_signature_id,
                    IncidentRecord.status.in_(ACTIVE_INCIDENT_STATUSES),
                )
            )
            if active_incident_id is not None:
                raise IncidentStateConflictError(
                    "Another active Incident already exists for this failure group"
                )
        changed = previous != target
        transition_id: UUID | None = None
        if changed:
            if not actor or len(actor) > 250:
                raise ValueError("actor must contain 1 to 250 characters")
            if reason is not None and len(reason) > 2000:
                raise ValueError("reason must not exceed 2000 characters")
            changed_at = now or datetime.now(UTC)
            incident.status = target
            incident.updated_at = changed_at
            transition = IncidentStateTransitionRecord(
                incident_id=incident.id,
                previous_status=previous,
                status=target,
                initiator=initiator,
                actor=actor,
                reason=reason,
                created_at=changed_at,
            )
            session.add(transition)
            session.flush()
            transition_id = transition.id
        return IncidentTransitionResult(
            incident_id=incident.id,
            previous_status=previous,
            status=target,
            changed=changed,
            transition_id=transition_id,
        )


def _find_or_create_incident(
    session: Session,
    *,
    failure: FailureEventRecord,
    error_signature_id: UUID | None,
) -> tuple[UUID, bool]:
    incident_id = uuid4()
    values = {
        "id": incident_id,
        "environment": failure.environment,
        "dag_id": failure.dag_id,
        "task_id": failure.task_id,
        "error_signature_id": error_signature_id,
        "initial_failure_event_id": failure.id,
        "status": IncidentStatus.OPEN,
    }
    if error_signature_id is None:
        session.add(IncidentRecord(**values))
        session.flush()
        return incident_id, True

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        inserted_id = session.scalar(
            postgresql_insert(IncidentRecord)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["environment", "dag_id", "task_id", "error_signature_id"],
                index_where=text(_ACTIVE_INCIDENT_PREDICATE),
            )
            .returning(IncidentRecord.id)
        )
    elif dialect_name == "sqlite":
        inserted_id = session.scalar(
            sqlite_insert(IncidentRecord)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["environment", "dag_id", "task_id", "error_signature_id"],
                index_where=text(_ACTIVE_INCIDENT_PREDICATE),
            )
            .returning(IncidentRecord.id)
        )
    else:  # pragma: no cover - only configured dialects are supported
        raise RuntimeError(f"Unsupported database dialect: {dialect_name}")
    if inserted_id is not None:
        return inserted_id, True

    existing_id = session.scalar(
        select(IncidentRecord.id).where(
            IncidentRecord.environment == failure.environment,
            IncidentRecord.dag_id == failure.dag_id,
            IncidentRecord.task_id == failure.task_id,
            IncidentRecord.error_signature_id == error_signature_id,
            IncidentRecord.status.in_(ACTIVE_INCIDENT_STATUSES),
        )
    )
    if existing_id is None:  # pragma: no cover - database invariant
        raise RuntimeError("Conflicting active Incident was not found")
    return existing_id, False
