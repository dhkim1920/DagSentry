"""Transactional append-only operator diagnosis persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.domain.diagnosis import DiagnosisValidationStatus
from dagsentry.domain.human_diagnosis import (
    HumanDiagnosisAction,
    HumanDiagnosisPublish,
    HumanDiagnosisWithdraw,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.models import (
    DiagnosisRecord,
    IncidentFailureRecord,
    IncidentHumanDiagnosisEvidenceRecord,
    IncidentHumanDiagnosisRecord,
    IncidentRecord,
)


class HumanDiagnosisNotFoundError(ValueError):
    """The referenced Incident was not found."""


class HumanDiagnosisConflictError(ValueError):
    """The requested revision does not match the latest revision."""


class HumanDiagnosisValidationError(ValueError):
    """A referenced Diagnosis, Evidence line, or input string is invalid."""


@dataclass(frozen=True)
class HumanDiagnosisResult:
    """One newly appended revision."""

    id: UUID
    incident_id: UUID
    revision: int
    action: HumanDiagnosisAction


def publish_human_diagnosis(
    session: Session,
    *,
    incident_id: UUID,
    request: HumanDiagnosisPublish,
    actor_user_id: UUID | None,
    actor_identity: str,
    log_processor: LogProcessor,
    now: datetime | None = None,
) -> HumanDiagnosisResult:
    """Append one validated operator diagnosis while holding the Incident lock."""
    _validate_actor(actor_identity)
    _reject_secrets(
        log_processor,
        request.root_cause,
        *request.recommended_actions,
        request.operator_notes,
        request.change_reason,
    )
    with session.begin():
        incident, previous = _locked_incident_and_previous(
            session, incident_id, request.expected_revision
        )
        if request.basis_diagnosis_id is not None:
            _incident_diagnosis(session, incident.id, request.basis_diagnosis_id)
        record = IncidentHumanDiagnosisRecord(
            incident_id=incident.id,
            revision=request.expected_revision + 1,
            action=HumanDiagnosisAction.PUBLISH,
            supersedes_id=previous.id if previous is not None else None,
            basis_diagnosis_id=request.basis_diagnosis_id,
            classification=request.classification,
            root_cause=request.root_cause,
            recommended_actions=list(request.recommended_actions),
            retry_decision=request.retry_decision,
            operator_notes=request.operator_notes,
            change_reason=request.change_reason,
            actor_user_id=actor_user_id,
            actor_identity=actor_identity,
            created_at=now or datetime.now(UTC),
        )
        session.add(record)
        session.flush()
        for position, reference in enumerate(request.evidence):
            source = _incident_diagnosis(session, incident.id, reference.source_diagnosis_id)
            content = _content_diagnosis(session, source)
            line = next(
                (
                    item
                    for item in content.evidence or []
                    if item.get("line_id") == reference.line_id
                ),
                None,
            )
            if line is None or not isinstance(line.get("text"), str):
                raise HumanDiagnosisValidationError("Evidence line is not available")
            session.add(
                IncidentHumanDiagnosisEvidenceRecord(
                    human_diagnosis_id=record.id,
                    source_diagnosis_id=source.id,
                    failure_event_id=source.failure_event_id,
                    line_id=reference.line_id,
                    text=line["text"],
                    position=position,
                )
            )
        return HumanDiagnosisResult(record.id, incident.id, record.revision, record.action)


def withdraw_human_diagnosis(
    session: Session,
    *,
    incident_id: UUID,
    request: HumanDiagnosisWithdraw,
    actor_user_id: UUID | None,
    actor_identity: str,
    log_processor: LogProcessor,
    now: datetime | None = None,
) -> HumanDiagnosisResult:
    """Append a withdrawal only when the current revision is published."""
    _validate_actor(actor_identity)
    _reject_secrets(log_processor, request.reason)
    with session.begin():
        incident, previous = _locked_incident_and_previous(
            session, incident_id, request.expected_revision
        )
        if previous is None or previous.action != HumanDiagnosisAction.PUBLISH:
            raise HumanDiagnosisConflictError(
                "No published human diagnosis is available to withdraw"
            )
        record = IncidentHumanDiagnosisRecord(
            incident_id=incident.id,
            revision=request.expected_revision + 1,
            action=HumanDiagnosisAction.WITHDRAW,
            supersedes_id=previous.id,
            basis_diagnosis_id=None,
            classification=None,
            root_cause=None,
            recommended_actions=None,
            retry_decision=None,
            operator_notes=None,
            change_reason=request.reason,
            actor_user_id=actor_user_id,
            actor_identity=actor_identity,
            created_at=now or datetime.now(UTC),
        )
        session.add(record)
        session.flush()
        return HumanDiagnosisResult(record.id, incident.id, record.revision, record.action)


def _locked_incident_and_previous(
    session: Session, incident_id: UUID, expected_revision: int
) -> tuple[IncidentRecord, IncidentHumanDiagnosisRecord | None]:
    incident = session.scalar(
        select(IncidentRecord).where(IncidentRecord.id == incident_id).with_for_update()
    )
    if incident is None:
        raise HumanDiagnosisNotFoundError("Incident not found")
    previous = session.scalar(
        select(IncidentHumanDiagnosisRecord)
        .where(IncidentHumanDiagnosisRecord.incident_id == incident_id)
        .order_by(
            IncidentHumanDiagnosisRecord.revision.desc(),
            IncidentHumanDiagnosisRecord.id.desc(),
        )
        .limit(1)
    )
    current_revision = previous.revision if previous is not None else 0
    if current_revision != expected_revision:
        raise HumanDiagnosisConflictError(f"Human diagnosis revision is {current_revision}")
    return incident, previous


def _incident_diagnosis(session: Session, incident_id: UUID, diagnosis_id: UUID) -> DiagnosisRecord:
    diagnosis = session.scalar(
        select(DiagnosisRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.failure_event_id == DiagnosisRecord.failure_event_id,
        )
        .where(
            IncidentFailureRecord.incident_id == incident_id,
            DiagnosisRecord.id == diagnosis_id,
            DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
        )
    )
    if diagnosis is None:
        raise HumanDiagnosisValidationError("Diagnosis is not a validated Incident diagnosis")
    return diagnosis


def _content_diagnosis(session: Session, diagnosis: DiagnosisRecord) -> DiagnosisRecord:
    if diagnosis.reused_from_diagnosis_id is None:
        return diagnosis
    original = session.get(DiagnosisRecord, diagnosis.reused_from_diagnosis_id)
    if original is None:  # pragma: no cover - database invariant
        raise HumanDiagnosisValidationError("Diagnosis content is unavailable")
    return original


def _reject_secrets(log_processor: LogProcessor, *values: str | None) -> None:
    if any(value is not None and log_processor.mask_secrets(value) != value for value in values):
        raise HumanDiagnosisValidationError("Human diagnosis input contains a secret")


def _validate_actor(actor_identity: str) -> None:
    if not actor_identity or len(actor_identity) > 250:
        raise HumanDiagnosisValidationError("Operator identity is invalid")
