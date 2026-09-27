"""Authenticated Incident query and operator transition routes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from dagsentry.db import get_session
from dagsentry.domain.diagnosis import (
    DiagnosisSource,
    DiagnosisValidationStatus,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import FailureState
from dagsentry.domain.human_diagnosis import HumanDiagnosisAction
from dagsentry.domain.identity import UserRole
from dagsentry.domain.incident import (
    IncidentStatus,
    IncidentTransitionInitiator,
)
from dagsentry.incident import (
    IncidentNotFoundError,
    IncidentStateConflictError,
    IncidentTransitionPermissionError,
    transition_incident,
)
from dagsentry.models import (
    DiagnosisRecord,
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentHumanDiagnosisRecord,
    IncidentRecord,
    IncidentStateTransitionRecord,
    NotificationDeliveryRecord,
)
from dagsentry.security import (
    AuthenticatedPrincipal,
    authenticate_query_principal,
    require_operator,
)

router = APIRouter(
    prefix="/api/v1/incidents",
    tags=["incidents"],
    dependencies=[Depends(authenticate_query_principal)],
)


class IncidentSummaryResponse(BaseModel):
    """Incident fields used in list and detail responses."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    environment: str
    dag_id: str
    task_id: str
    error_signature_id: UUID | None
    exception_class: str | None = None
    normalized_message: str | None = None
    status: IncidentStatus
    failure_count: int
    first_failure_at: datetime
    last_failure_at: datetime
    created_at: datetime
    updated_at: datetime


class IncidentListResponse(BaseModel):
    """Stable offset-paginated Incident list."""

    model_config = ConfigDict(extra="forbid")

    items: list[IncidentSummaryResponse]
    total: int
    limit: int
    offset: int


class IncidentDiagnosisEvidenceResponse(BaseModel):
    """One exact sanitized Evidence line cited by a Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    line_id: int
    text: str


class IncidentDiagnosisExtractedValueResponse(BaseModel):
    """One value captured by a deterministic diagnosis rule."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class IncidentDiagnosisResponse(BaseModel):
    """One Diagnosis attempt with effective and reuse provenance."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    effective: bool
    source: DiagnosisSource
    validation_status: DiagnosisValidationStatus
    content_diagnosis_id: UUID
    reused_from_diagnosis_id: UUID | None
    classification: ErrorClassification | None
    root_cause: str | None
    confidence: float | None
    confidence_reason: str | None
    matched_rule: str | None
    extracted_values: list[IncidentDiagnosisExtractedValueResponse]
    evidence: list[IncidentDiagnosisEvidenceResponse]
    recommended_actions: list[str]
    retry_decision: RetryDecision | None
    operator_review_required: bool | None
    validation_errors: list[str]
    diagnosis_schema_version: int
    prompt_version: str | None
    rule_version: int | None
    created_at: datetime


class IncidentErrorSignatureResponse(BaseModel):
    """Versioned Error Signature related to a Failure Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    fingerprint_version: int
    fingerprint: str
    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None
    created_at: datetime


class IncidentFailureResponse(BaseModel):
    """One Failure Event linked to an Incident."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    dag_run_id: str
    map_index: int
    try_number: int
    state: FailureState
    observed_at: datetime
    diagnoses: list[IncidentDiagnosisResponse]
    error_signature: IncidentErrorSignatureResponse | None
    airflow_log_url: str | None
    is_initial_failure: bool
    is_final_failure: bool


class IncidentTransitionHistoryResponse(BaseModel):
    """One immutable state transition audit entry."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    previous_status: IncidentStatus
    status: IncidentStatus
    initiator: IncidentTransitionInitiator
    actor: str
    reason: str | None
    created_at: datetime


class CurrentHumanDiagnosisResponse(BaseModel):
    """Current operator-confirmed Incident diagnosis, separate from automatic effective output."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    revision: int
    classification: ErrorClassification
    root_cause: str
    recommended_actions: list[str]
    retry_decision: RetryDecision
    operator_notes: str | None
    actor_identity: str
    created_at: datetime


class IncidentDetailResponse(BaseModel):
    """Incident with linked failures and complete state history."""

    model_config = ConfigDict(extra="forbid")

    incident: IncidentSummaryResponse
    failures: list[IncidentFailureResponse]
    transitions: list[IncidentTransitionHistoryResponse]
    current_human_diagnosis: CurrentHumanDiagnosisResponse | None


class OperatorIncidentTarget(StrEnum):
    """Incident states exposed through the operator API."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class IncidentSort(StrEnum):
    """Allowlisted server-side Incident list sort fields."""

    LAST_FAILURE_AT = "last_failure_at"
    FAILURE_COUNT = "failure_count"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class SortOrder(StrEnum):
    """Stable list ordering directions."""

    ASC = "asc"
    DESC = "desc"


class IncidentTransitionRequest(BaseModel):
    """Optimistic operator request for one Incident state change."""

    model_config = ConfigDict(extra="forbid")

    status: OperatorIncidentTarget
    expected_status: IncidentStatus
    reason: Annotated[str | None, Field(min_length=1, max_length=2000)] = None


class IncidentTransitionResponse(BaseModel):
    """Result of an operator state transition."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    previous_status: IncidentStatus
    status: IncidentStatus
    changed: bool
    transition_id: UUID | None


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    session: Annotated[Session, Depends(get_session)],
    incident_status: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    dag_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    task_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    sort_by: Annotated[IncidentSort, Query(alias="sort")] = IncidentSort.LAST_FAILURE_AT,
    order: SortOrder = SortOrder.DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IncidentListResponse:
    """List Incidents using bounded filters and stable ordering."""
    filters: list[ColumnElement[bool]] = []
    if incident_status is not None:
        filters.append(IncidentRecord.status == incident_status)
    if environment is not None:
        filters.append(IncidentRecord.environment == environment)
    if dag_id is not None:
        filters.append(IncidentRecord.dag_id == dag_id)
    if task_id is not None:
        filters.append(IncidentRecord.task_id == task_id)

    failure_stats = (
        select(
            IncidentFailureRecord.incident_id,
            func.count(FailureEventRecord.id).label("failure_count"),
            func.min(FailureEventRecord.observed_at).label("first_failure_at"),
            func.max(FailureEventRecord.observed_at).label("last_failure_at"),
        )
        .join(
            FailureEventRecord,
            FailureEventRecord.id == IncidentFailureRecord.failure_event_id,
        )
        .group_by(IncidentFailureRecord.incident_id)
        .subquery()
    )
    sort_columns = {
        IncidentSort.LAST_FAILURE_AT: failure_stats.c.last_failure_at,
        IncidentSort.FAILURE_COUNT: failure_stats.c.failure_count,
        IncidentSort.CREATED_AT: IncidentRecord.created_at,
        IncidentSort.UPDATED_AT: IncidentRecord.updated_at,
    }
    sort_column = sort_columns[sort_by]
    primary_order = sort_column.asc() if order == SortOrder.ASC else sort_column.desc()
    stable_order = IncidentRecord.id.asc() if order == SortOrder.ASC else IncidentRecord.id.desc()
    rows = session.execute(
        select(
            IncidentRecord,
            failure_stats.c.failure_count,
            failure_stats.c.first_failure_at,
            failure_stats.c.last_failure_at,
            ErrorSignatureRecord,
        )
        .join(failure_stats, failure_stats.c.incident_id == IncidentRecord.id)
        .outerjoin(
            ErrorSignatureRecord, ErrorSignatureRecord.id == IncidentRecord.error_signature_id
        )
        .where(*filters)
        .order_by(primary_order, stable_order)
        .limit(limit)
        .offset(offset)
    ).all()
    total = session.scalar(select(func.count()).select_from(IncidentRecord).where(*filters)) or 0
    return IncidentListResponse(
        items=[
            _summary(
                record,
                int(failure_count),
                first_failure_at,
                last_failure_at,
                signature=signature,
            )
            for record, failure_count, first_failure_at, last_failure_at, signature in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(
    incident_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> IncidentDetailResponse:
    """Return one Incident, its Failures, and its state transition history."""
    incident = session.get(IncidentRecord, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    failures = session.scalars(
        select(FailureEventRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.failure_event_id == FailureEventRecord.id,
        )
        .where(IncidentFailureRecord.incident_id == incident_id)
        .order_by(FailureEventRecord.observed_at, FailureEventRecord.id)
    ).all()
    transitions = session.scalars(
        select(IncidentStateTransitionRecord)
        .where(IncidentStateTransitionRecord.incident_id == incident_id)
        .order_by(
            IncidentStateTransitionRecord.created_at,
            IncidentStateTransitionRecord.id,
        )
    ).all()
    latest_human_diagnosis = session.scalar(
        select(IncidentHumanDiagnosisRecord)
        .where(IncidentHumanDiagnosisRecord.incident_id == incident_id)
        .order_by(
            IncidentHumanDiagnosisRecord.revision.desc(),
            IncidentHumanDiagnosisRecord.id.desc(),
        )
        .limit(1)
    )
    if not failures:  # pragma: no cover - database invariant
        raise RuntimeError("Incident has no linked Failure Events")

    failure_ids = [failure.id for failure in failures]
    diagnoses = session.scalars(
        select(DiagnosisRecord)
        .where(DiagnosisRecord.failure_event_id.in_(failure_ids))
        .order_by(DiagnosisRecord.created_at, DiagnosisRecord.id)
    ).all()
    diagnosis_ids = [diagnosis.id for diagnosis in diagnoses]
    deliveries = (
        session.scalars(
            select(NotificationDeliveryRecord).where(
                NotificationDeliveryRecord.diagnosis_id.in_(diagnosis_ids)
            )
        ).all()
        if diagnosis_ids
        else []
    )
    deliveries_by_diagnosis = {delivery.diagnosis_id: delivery for delivery in deliveries}

    diagnoses_by_failure: dict[UUID, list[DiagnosisRecord]] = {}
    for diagnosis in diagnoses:
        diagnoses_by_failure.setdefault(diagnosis.failure_event_id, []).append(diagnosis)

    diagnosis_content_by_id = {diagnosis.id: diagnosis for diagnosis in diagnoses}
    reused_from_ids = {
        diagnosis.reused_from_diagnosis_id
        for diagnosis in diagnoses
        if diagnosis.reused_from_diagnosis_id is not None
    }
    missing_content_ids = reused_from_ids - diagnosis_content_by_id.keys()
    if missing_content_ids:
        original_diagnoses = session.scalars(
            select(DiagnosisRecord).where(DiagnosisRecord.id.in_(missing_content_ids))
        ).all()
        diagnosis_content_by_id.update(
            {diagnosis.id: diagnosis for diagnosis in original_diagnoses}
        )

    signature_ids = {
        diagnosis.error_signature_id
        for diagnosis in diagnoses
        if diagnosis.error_signature_id is not None
    }
    if incident.error_signature_id is not None:
        signature_ids.add(incident.error_signature_id)
    signatures = (
        session.scalars(
            select(ErrorSignatureRecord).where(ErrorSignatureRecord.id.in_(signature_ids))
        ).all()
        if signature_ids
        else []
    )
    signatures_by_id = {signature.id: signature for signature in signatures}
    current_human_diagnosis: CurrentHumanDiagnosisResponse | None = None
    if (
        latest_human_diagnosis is not None
        and latest_human_diagnosis.action == HumanDiagnosisAction.PUBLISH
    ):
        assert latest_human_diagnosis.classification is not None
        assert latest_human_diagnosis.root_cause is not None
        assert latest_human_diagnosis.retry_decision is not None
        current_human_diagnosis = CurrentHumanDiagnosisResponse(
            id=latest_human_diagnosis.id,
            revision=latest_human_diagnosis.revision,
            classification=latest_human_diagnosis.classification,
            root_cause=latest_human_diagnosis.root_cause,
            recommended_actions=list(latest_human_diagnosis.recommended_actions or []),
            retry_decision=latest_human_diagnosis.retry_decision,
            operator_notes=latest_human_diagnosis.operator_notes,
            actor_identity=latest_human_diagnosis.actor_identity,
            created_at=_as_utc(latest_human_diagnosis.created_at),
        )

    return IncidentDetailResponse(
        incident=_summary(
            incident,
            len(failures),
            failures[0].observed_at,
            failures[-1].observed_at,
            signature=(
                signatures_by_id.get(incident.error_signature_id)
                if incident.error_signature_id is not None
                else None
            ),
        ),
        failures=[
            _failure_response(
                failure,
                diagnoses=diagnoses_by_failure.get(failure.id, []),
                diagnosis_content_by_id=diagnosis_content_by_id,
                deliveries_by_diagnosis=deliveries_by_diagnosis,
                signatures_by_id=signatures_by_id,
                incident_error_signature_id=incident.error_signature_id,
                initial_failure_event_id=incident.initial_failure_event_id,
                final_failure_event_id=incident.final_failure_event_id,
            )
            for failure in failures
        ],
        transitions=[
            IncidentTransitionHistoryResponse(
                id=transition.id,
                previous_status=transition.previous_status,
                status=transition.status,
                initiator=transition.initiator,
                actor=transition.actor,
                reason=transition.reason,
                created_at=_as_utc(transition.created_at),
            )
            for transition in transitions
        ],
        current_human_diagnosis=current_human_diagnosis,
    )


@router.patch("/{incident_id}/status", response_model=IncidentTransitionResponse)
def update_incident_status(
    incident_id: UUID,
    body: IncidentTransitionRequest,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_operator)],
) -> IncidentTransitionResponse:
    """Apply an operator transition without overwriting a concurrent change."""
    try:
        result = transition_incident(
            session,
            incident_id=incident_id,
            target=IncidentStatus(body.status.value),
            initiator=IncidentTransitionInitiator.OPERATOR,
            actor=_operator_identity(principal),
            reason=body.reason,
            expected_status=body.expected_status,
            allow_terminal_override=principal.role == UserRole.ADMIN,
        )
    except IncidentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        ) from error
    except IncidentTransitionPermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except (IncidentStateConflictError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return IncidentTransitionResponse(
        incident_id=result.incident_id,
        previous_status=result.previous_status,
        status=result.status,
        changed=result.changed,
        transition_id=result.transition_id,
    )


def _operator_identity(principal: AuthenticatedPrincipal) -> str:
    identity = principal.identity
    if identity is None:  # pragma: no cover - require_operator invariant
        raise RuntimeError("Operator principal has no identity")
    return identity


def _summary(
    record: IncidentRecord,
    failure_count: int,
    first_failure_at: datetime,
    last_failure_at: datetime,
    *,
    signature: ErrorSignatureRecord | None,
) -> IncidentSummaryResponse:
    return IncidentSummaryResponse(
        id=record.id,
        environment=record.environment,
        dag_id=record.dag_id,
        task_id=record.task_id,
        error_signature_id=record.error_signature_id,
        exception_class=signature.exception_class if signature else None,
        normalized_message=signature.normalized_message if signature else None,
        status=record.status,
        failure_count=failure_count,
        first_failure_at=_as_utc(first_failure_at),
        last_failure_at=_as_utc(last_failure_at),
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )


def _failure_response(
    failure: FailureEventRecord,
    *,
    diagnoses: list[DiagnosisRecord],
    diagnosis_content_by_id: dict[UUID, DiagnosisRecord],
    deliveries_by_diagnosis: dict[UUID, NotificationDeliveryRecord],
    signatures_by_id: dict[UUID, ErrorSignatureRecord],
    incident_error_signature_id: UUID | None,
    initial_failure_event_id: UUID,
    final_failure_event_id: UUID | None,
) -> IncidentFailureResponse:
    effective = next(
        (diagnosis for diagnosis in reversed(diagnoses) if diagnosis.id in deliveries_by_diagnosis),
        None,
    )
    if effective is None:
        effective = next(
            (
                diagnosis
                for diagnosis in reversed(diagnoses)
                if diagnosis.validation_status == DiagnosisValidationStatus.PASSED
            ),
            None,
        )

    signature_id = (
        effective.error_signature_id
        if effective is not None and effective.error_signature_id is not None
        else incident_error_signature_id
    )
    signature = signatures_by_id.get(signature_id) if signature_id is not None else None
    delivery = deliveries_by_diagnosis.get(effective.id) if effective is not None else None
    airflow_log_url = delivery.payload.get("airflow_log_url") if delivery is not None else None
    if not isinstance(airflow_log_url, str) or not airflow_log_url:
        airflow_log_url = None

    return IncidentFailureResponse(
        id=failure.id,
        dag_run_id=failure.dag_run_id,
        map_index=failure.map_index,
        try_number=failure.try_number,
        state=failure.state,
        is_initial_failure=failure.id == initial_failure_event_id,
        is_final_failure=failure.id == final_failure_event_id,
        observed_at=_as_utc(failure.observed_at),
        diagnoses=[
            _diagnosis_response(
                diagnosis,
                effective=diagnosis.id == (effective.id if effective is not None else None),
                diagnosis_content_by_id=diagnosis_content_by_id,
            )
            for diagnosis in diagnoses
        ],
        error_signature=_signature_response(signature) if signature is not None else None,
        airflow_log_url=airflow_log_url,
    )


def _diagnosis_response(
    diagnosis: DiagnosisRecord,
    *,
    effective: bool,
    diagnosis_content_by_id: dict[UUID, DiagnosisRecord],
) -> IncidentDiagnosisResponse:
    content = diagnosis
    if diagnosis.reused_from_diagnosis_id is not None:
        content = diagnosis_content_by_id.get(diagnosis.reused_from_diagnosis_id, diagnosis)
    return IncidentDiagnosisResponse(
        id=diagnosis.id,
        effective=effective,
        source=diagnosis.source,
        validation_status=diagnosis.validation_status,
        content_diagnosis_id=content.id,
        reused_from_diagnosis_id=diagnosis.reused_from_diagnosis_id,
        classification=content.classification,
        root_cause=content.root_cause,
        confidence=content.confidence,
        confidence_reason=content.confidence_reason,
        matched_rule=content.matched_rule,
        extracted_values=[
            IncidentDiagnosisExtractedValueResponse.model_validate(value)
            for value in content.extracted_values or []
        ],
        evidence=[
            IncidentDiagnosisEvidenceResponse.model_validate(item)
            for item in content.evidence or []
        ],
        recommended_actions=list(content.recommended_actions or []),
        retry_decision=content.retry_decision,
        operator_review_required=content.operator_review_required,
        validation_errors=list(diagnosis.validation_errors or []),
        diagnosis_schema_version=diagnosis.diagnosis_schema_version,
        prompt_version=diagnosis.prompt_version,
        rule_version=diagnosis.rule_version,
        created_at=_as_utc(diagnosis.created_at),
    )


def _signature_response(record: ErrorSignatureRecord) -> IncidentErrorSignatureResponse:
    return IncidentErrorSignatureResponse(
        id=record.id,
        fingerprint_version=record.fingerprint_version,
        fingerprint=record.fingerprint,
        operator_type=record.operator_type,
        exception_class=record.exception_class,
        vendor_error_code=record.vendor_error_code,
        normalized_message=record.normalized_message,
        application_stack_frame=record.application_stack_frame,
        created_at=_as_utc(record.created_at),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
