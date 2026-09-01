"""Authenticated append-only operator diagnosis routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.db import get_session
from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision
from dagsentry.domain.human_diagnosis import (
    HumanDiagnosisAction,
    HumanDiagnosisEvidenceReference,
    HumanDiagnosisPublish,
    HumanDiagnosisWithdraw,
)
from dagsentry.human_diagnosis import (
    HumanDiagnosisConflictError,
    HumanDiagnosisNotFoundError,
    HumanDiagnosisValidationError,
    publish_human_diagnosis,
    withdraw_human_diagnosis,
)
from dagsentry.log_processing import LogProcessingConfig, LogProcessor
from dagsentry.models import IncidentHumanDiagnosisEvidenceRecord, IncidentHumanDiagnosisRecord
from dagsentry.security import (
    AuthenticatedPrincipal,
    authenticate_query_principal,
    require_operator,
)

router = APIRouter(
    prefix="/api/v1/incidents/{incident_id}/human-diagnoses",
    tags=["human-diagnoses"],
    dependencies=[Depends(authenticate_query_principal)],
)


class HumanDiagnosisEvidenceRequest(BaseModel):
    """A client-selected stored Evidence reference; text is server-derived."""

    model_config = ConfigDict(extra="forbid")

    source_diagnosis_id: UUID
    line_id: Annotated[int, Field(ge=1)]


class HumanDiagnosisPublishRequest(BaseModel):
    """One immutable operator diagnosis publication request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: Annotated[int, Field(ge=0)]
    basis_diagnosis_id: UUID | None = None
    classification: ErrorClassification
    root_cause: Annotated[str, Field(min_length=1, max_length=4_000)]
    recommended_actions: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list, max_length=10
    )
    retry_decision: RetryDecision
    evidence: list[HumanDiagnosisEvidenceRequest] = Field(default_factory=list, max_length=20)
    operator_notes: Annotated[str | None, Field(min_length=1, max_length=4_000)] = None
    change_reason: Annotated[str | None, Field(min_length=1, max_length=1_000)] = None


class HumanDiagnosisWithdrawRequest(BaseModel):
    """One append-only operator diagnosis withdrawal request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1, max_length=1_000)]


class HumanDiagnosisEvidenceResponse(BaseModel):
    """A sanitized stored Evidence snapshot."""

    model_config = ConfigDict(extra="forbid")

    source_diagnosis_id: UUID
    failure_event_id: UUID
    line_id: int
    text: str
    position: int


class HumanDiagnosisResponse(BaseModel):
    """One append-only human diagnosis revision."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    incident_id: UUID
    revision: int
    action: HumanDiagnosisAction
    supersedes_id: UUID | None
    basis_diagnosis_id: UUID | None
    classification: ErrorClassification | None
    root_cause: str | None
    recommended_actions: list[str]
    retry_decision: RetryDecision | None
    operator_notes: str | None
    change_reason: str | None
    actor_identity: str
    created_at: datetime
    evidence: list[HumanDiagnosisEvidenceResponse]


class HumanDiagnosisHistoryResponse(BaseModel):
    """Bounded Incident history plus the current published revision when present."""

    model_config = ConfigDict(extra="forbid")

    current_human_diagnosis_id: UUID | None
    items: list[HumanDiagnosisResponse]


@router.get("", response_model=HumanDiagnosisHistoryResponse)
def list_human_diagnoses(
    incident_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> HumanDiagnosisHistoryResponse:
    """Return immutable human diagnosis revisions newest first."""
    records = session.scalars(
        select(IncidentHumanDiagnosisRecord)
        .where(IncidentHumanDiagnosisRecord.incident_id == incident_id)
        .order_by(
            IncidentHumanDiagnosisRecord.revision.desc(),
            IncidentHumanDiagnosisRecord.id.desc(),
        )
        .limit(100)
    ).all()
    current = records[0] if records and records[0].action == HumanDiagnosisAction.PUBLISH else None
    evidence_by_human_id = _evidence_by_human_id(session, {record.id for record in records})
    return HumanDiagnosisHistoryResponse(
        current_human_diagnosis_id=current.id if current is not None else None,
        items=[_response(record, evidence_by_human_id) for record in records],
    )


@router.get("/{human_diagnosis_id}", response_model=HumanDiagnosisResponse)
def get_human_diagnosis(
    incident_id: UUID,
    human_diagnosis_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> HumanDiagnosisResponse:
    """Return one immutable human diagnosis revision for this Incident."""
    record = session.scalar(
        select(IncidentHumanDiagnosisRecord).where(
            IncidentHumanDiagnosisRecord.id == human_diagnosis_id,
            IncidentHumanDiagnosisRecord.incident_id == incident_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Human diagnosis not found"
        )
    return _response(record, _evidence_by_human_id(session, {record.id}))


@router.post("", response_model=HumanDiagnosisResponse, status_code=status.HTTP_201_CREATED)
def publish(
    incident_id: UUID,
    body: HumanDiagnosisPublishRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_operator)],
) -> HumanDiagnosisResponse:
    """Append an operator-confirmed Diagnosis without modifying automatic results."""
    if body.expected_revision > 0 and body.change_reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="change_reason is required after the first revision",
        )
    references = tuple(
        HumanDiagnosisEvidenceReference(
            source_diagnosis_id=item.source_diagnosis_id,
            line_id=item.line_id,
        )
        for item in body.evidence
    )
    if len({(item.source_diagnosis_id, item.line_id) for item in references}) != len(references):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Evidence references must be unique",
        )
    try:
        result = publish_human_diagnosis(
            session,
            incident_id=incident_id,
            request=HumanDiagnosisPublish(
                expected_revision=body.expected_revision,
                basis_diagnosis_id=body.basis_diagnosis_id,
                classification=body.classification,
                root_cause=body.root_cause,
                recommended_actions=tuple(body.recommended_actions),
                retry_decision=body.retry_decision,
                evidence=references,
                operator_notes=body.operator_notes,
                change_reason=body.change_reason,
            ),
            actor_user_id=principal.user_id,
            actor_identity=_actor_identity(principal),
            log_processor=_log_processor(request),
        )
    except HumanDiagnosisNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found"
        ) from error
    except HumanDiagnosisConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except HumanDiagnosisValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    record = session.get(IncidentHumanDiagnosisRecord, result.id)
    assert record is not None  # session uses expire_on_commit=False
    return _response(record, _evidence_by_human_id(session, {record.id}))


@router.post(
    "/withdraw", response_model=HumanDiagnosisResponse, status_code=status.HTTP_201_CREATED
)
def withdraw(
    incident_id: UUID,
    body: HumanDiagnosisWithdrawRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_operator)],
) -> HumanDiagnosisResponse:
    """Append a withdrawal while retaining all earlier operator content."""
    try:
        result = withdraw_human_diagnosis(
            session,
            incident_id=incident_id,
            request=HumanDiagnosisWithdraw(body.expected_revision, body.reason),
            actor_user_id=principal.user_id,
            actor_identity=_actor_identity(principal),
            log_processor=_log_processor(request),
        )
    except HumanDiagnosisNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found"
        ) from error
    except HumanDiagnosisConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except HumanDiagnosisValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    record = session.get(IncidentHumanDiagnosisRecord, result.id)
    assert record is not None
    return _response(record, {})


def _evidence_by_human_id(
    session: Session, human_diagnosis_ids: set[UUID]
) -> dict[UUID, list[IncidentHumanDiagnosisEvidenceRecord]]:
    if not human_diagnosis_ids:
        return {}
    records = session.scalars(
        select(IncidentHumanDiagnosisEvidenceRecord)
        .where(IncidentHumanDiagnosisEvidenceRecord.human_diagnosis_id.in_(human_diagnosis_ids))
        .order_by(IncidentHumanDiagnosisEvidenceRecord.position)
    ).all()
    grouped: dict[UUID, list[IncidentHumanDiagnosisEvidenceRecord]] = {}
    for record in records:
        grouped.setdefault(record.human_diagnosis_id, []).append(record)
    return grouped


def _response(
    record: IncidentHumanDiagnosisRecord,
    evidence_by_human_id: dict[UUID, list[IncidentHumanDiagnosisEvidenceRecord]],
) -> HumanDiagnosisResponse:
    return HumanDiagnosisResponse(
        id=record.id,
        incident_id=record.incident_id,
        revision=record.revision,
        action=record.action,
        supersedes_id=record.supersedes_id,
        basis_diagnosis_id=record.basis_diagnosis_id,
        classification=record.classification,
        root_cause=record.root_cause,
        recommended_actions=list(record.recommended_actions or []),
        retry_decision=record.retry_decision,
        operator_notes=record.operator_notes,
        change_reason=record.change_reason,
        actor_identity=record.actor_identity,
        created_at=_as_utc(record.created_at),
        evidence=[
            HumanDiagnosisEvidenceResponse(
                source_diagnosis_id=item.source_diagnosis_id,
                failure_event_id=item.failure_event_id,
                line_id=item.line_id,
                text=item.text,
                position=item.position,
            )
            for item in evidence_by_human_id.get(record.id, [])
        ],
    )


def _log_processor(request: Request) -> LogProcessor:
    return LogProcessor(LogProcessingConfig.from_settings(request.app.state.settings))


def _actor_identity(principal: AuthenticatedPrincipal) -> str:
    if principal.identity is None:  # pragma: no cover - require_operator invariant
        raise RuntimeError("Operator principal has no identity")
    return principal.identity


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
