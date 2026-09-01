"""Authenticated Diagnosis History query routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, case, cast, func, literal, select, union_all
from sqlalchemy.orm import Session, aliased
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
from dagsentry.models import (
    DiagnosisRecord,
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentHumanDiagnosisRecord,
    IncidentRecord,
    NotificationDeliveryRecord,
)
from dagsentry.security import authenticate_query_principal

router = APIRouter(
    prefix="/api/v1/diagnoses",
    tags=["diagnoses"],
    dependencies=[Depends(authenticate_query_principal)],
)


class DiagnosisSummaryResponse(BaseModel):
    """One Diagnosis History row with resolved effective provenance."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    failure_event_id: UUID
    error_signature_id: UUID | None
    effective: bool
    effective_diagnosis_id: UUID | None
    source: DiagnosisSource
    validation_status: DiagnosisValidationStatus
    content_diagnosis_id: UUID
    reused_from_diagnosis_id: UUID | None
    classification: ErrorClassification | None
    root_cause: str | None
    confidence: float | None
    operator_review_required: bool | None
    diagnosis_schema_version: int
    prompt_version: str | None
    rule_version: int | None
    created_at: datetime


class DiagnosisListResponse(BaseModel):
    """Bounded Diagnosis History list."""

    model_config = ConfigDict(extra="forbid")

    items: list[DiagnosisSummaryResponse]
    total: int
    limit: int
    offset: int


class DiagnosisHistorySource(StrEnum):
    """Actor type displayed by the unified Diagnosis History."""

    AI = "AI"
    RULE = "RULE"
    OPERATOR = "OPERATOR"


class DiagnosisHistoryItemResponse(BaseModel):
    """One automatic or operator-authored diagnosis event for audit search."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    incident_id: UUID | None
    error_signature_id: UUID | None
    source_type: DiagnosisHistorySource
    root_cause: str | None
    status: str
    actor_identity: str | None
    revision: int | None
    reused_from_diagnosis_id: UUID | None
    created_at: datetime


class DiagnosisHistoryResponse(BaseModel):
    """Bounded unified automatic and operator diagnosis history."""

    model_config = ConfigDict(extra="forbid")

    items: list[DiagnosisHistoryItemResponse]
    total: int
    limit: int
    offset: int


class DiagnosisEvidenceResponse(BaseModel):
    """One exact sanitized Evidence line."""

    model_config = ConfigDict(extra="forbid")

    line_id: int
    text: str


class DiagnosisExtractedValueResponse(BaseModel):
    """One deterministic value extracted during Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class DiagnosisFailureResponse(BaseModel):
    """Failure Try context for one Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    environment: str
    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    state: FailureState
    observed_at: datetime


class DiagnosisErrorSignatureResponse(BaseModel):
    """Canonical Error Signature context for one Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    fingerprint_version: int
    fingerprint: str
    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None


class DiagnosisDetailResponse(DiagnosisSummaryResponse):
    """Complete sanitized Diagnosis content and operational context."""

    confidence_reason: str | None
    matched_rule: str | None
    extracted_values: list[DiagnosisExtractedValueResponse]
    evidence: list[DiagnosisEvidenceResponse]
    recommended_actions: list[str]
    retry_decision: RetryDecision | None
    validation_errors: list[str]
    failure: DiagnosisFailureResponse
    incident_id: UUID | None
    error_signature: DiagnosisErrorSignatureResponse | None
    airflow_log_url: str | None


class DiagnosisSort(StrEnum):
    """Allowlisted Diagnosis History sort fields."""

    CREATED_AT = "created_at"
    CONFIDENCE = "confidence"


class SortOrder(StrEnum):
    """Stable list ordering directions."""

    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class DiagnosisQueryContext:
    """Batch-loaded relationships needed to resolve Diagnosis provenance."""

    content_by_id: dict[UUID, DiagnosisRecord]
    effective_by_failure: dict[UUID, DiagnosisRecord]
    deliveries_by_diagnosis: dict[UUID, NotificationDeliveryRecord]


@router.get("", response_model=DiagnosisListResponse)
def list_diagnoses(
    session: Annotated[Session, Depends(get_session)],
    source: DiagnosisSource | None = None,
    validation_status: DiagnosisValidationStatus | None = None,
    classification: ErrorClassification | None = None,
    error_signature_id: UUID | None = None,
    failure_event_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: Annotated[DiagnosisSort, Query(alias="sort")] = DiagnosisSort.CREATED_AT,
    order: SortOrder = SortOrder.DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiagnosisListResponse:
    """List Diagnosis History with bounded filters and stable ordering."""
    _validate_date_order(date_from, date_to)
    original = aliased(DiagnosisRecord)
    resolved_classification = func.coalesce(
        DiagnosisRecord.classification,
        original.classification,
    )
    resolved_confidence = func.coalesce(DiagnosisRecord.confidence, original.confidence)
    filters: list[ColumnElement[bool]] = []
    if source is not None:
        filters.append(DiagnosisRecord.source == source)
    if validation_status is not None:
        filters.append(DiagnosisRecord.validation_status == validation_status)
    if classification is not None:
        filters.append(resolved_classification == classification)
    if error_signature_id is not None:
        filters.append(DiagnosisRecord.error_signature_id == error_signature_id)
    if failure_event_id is not None:
        filters.append(DiagnosisRecord.failure_event_id == failure_event_id)
    if date_from is not None:
        filters.append(DiagnosisRecord.created_at >= _day_start(date_from))
    if date_to is not None:
        filters.append(DiagnosisRecord.created_at < _day_start(date_to + timedelta(days=1)))

    sort_columns = {
        DiagnosisSort.CREATED_AT: DiagnosisRecord.created_at,
        DiagnosisSort.CONFIDENCE: resolved_confidence,
    }
    sort_column = sort_columns[sort_by]
    primary_order = sort_column.asc() if order == SortOrder.ASC else sort_column.desc()
    if sort_by == DiagnosisSort.CONFIDENCE:
        primary_order = primary_order.nulls_last()
    stable_order = DiagnosisRecord.id.asc() if order == SortOrder.ASC else DiagnosisRecord.id.desc()
    join_condition = DiagnosisRecord.reused_from_diagnosis_id == original.id
    records = session.scalars(
        select(DiagnosisRecord)
        .outerjoin(original, join_condition)
        .where(*filters)
        .order_by(primary_order, stable_order)
        .limit(limit)
        .offset(offset)
    ).all()
    total = (
        session.scalar(
            select(func.count())
            .select_from(DiagnosisRecord)
            .outerjoin(original, join_condition)
            .where(*filters)
        )
        or 0
    )
    context = _load_context(
        session,
        {record.failure_event_id for record in records},
    )
    return DiagnosisListResponse(
        items=[_summary_response(record, context) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/history", response_model=DiagnosisHistoryResponse)
def list_diagnosis_history(
    session: Annotated[Session, Depends(get_session)],
    source_type: DiagnosisHistorySource | None = None,
    error_signature_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiagnosisHistoryResponse:
    """Search automatic and operator diagnoses without merging their storage."""
    _validate_date_order(date_from, date_to)
    original = aliased(DiagnosisRecord)
    normalized_source = func.coalesce(original.source, DiagnosisRecord.source)
    automatic_filters: list[ColumnElement[bool]] = []
    human_filters: list[ColumnElement[bool]] = []
    if source_type is not None:
        if source_type == DiagnosisHistorySource.OPERATOR:
            automatic_filters.append(literal(False))
        else:
            automatic_filters.append(normalized_source == DiagnosisSource(source_type.value))
            human_filters.append(literal(False))
    if error_signature_id is not None:
        automatic_filters.append(DiagnosisRecord.error_signature_id == error_signature_id)
        human_filters.append(IncidentRecord.error_signature_id == error_signature_id)
    if date_from is not None:
        automatic_filters.append(DiagnosisRecord.created_at >= _day_start(date_from))
        human_filters.append(IncidentHumanDiagnosisRecord.created_at >= _day_start(date_from))
    if date_to is not None:
        end = _day_start(date_to + timedelta(days=1))
        automatic_filters.append(DiagnosisRecord.created_at < end)
        human_filters.append(IncidentHumanDiagnosisRecord.created_at < end)

    automatic = (
        select(
            DiagnosisRecord.id.label("id"),
            IncidentFailureRecord.incident_id.label("incident_id"),
            DiagnosisRecord.error_signature_id.label("error_signature_id"),
            cast(normalized_source, String).label("source_type"),
            func.coalesce(DiagnosisRecord.root_cause, original.root_cause).label("root_cause"),
            cast(DiagnosisRecord.validation_status, String).label("status"),
            literal(None, String).label("actor_identity"),
            literal(None).label("revision"),
            DiagnosisRecord.reused_from_diagnosis_id.label("reused_from_diagnosis_id"),
            DiagnosisRecord.created_at.label("created_at"),
        )
        .outerjoin(original, DiagnosisRecord.reused_from_diagnosis_id == original.id)
        .outerjoin(
            IncidentFailureRecord,
            IncidentFailureRecord.failure_event_id == DiagnosisRecord.failure_event_id,
        )
        .where(*automatic_filters)
    )
    latest_human_revision = (
        select(
            IncidentHumanDiagnosisRecord.incident_id.label("incident_id"),
            func.max(IncidentHumanDiagnosisRecord.revision).label("revision"),
        )
        .group_by(IncidentHumanDiagnosisRecord.incident_id)
        .subquery()
    )
    human = (
        select(
            IncidentHumanDiagnosisRecord.id.label("id"),
            IncidentHumanDiagnosisRecord.incident_id.label("incident_id"),
            IncidentRecord.error_signature_id.label("error_signature_id"),
            literal(DiagnosisHistorySource.OPERATOR.value).label("source_type"),
            IncidentHumanDiagnosisRecord.root_cause.label("root_cause"),
            case(
                (IncidentHumanDiagnosisRecord.action == HumanDiagnosisAction.WITHDRAW, "WITHDRAWN"),
                (IncidentHumanDiagnosisRecord.revision > 1, "UPDATED"),
                else_="CONFIRMED",
            ).label("status"),
            IncidentHumanDiagnosisRecord.actor_identity.label("actor_identity"),
            IncidentHumanDiagnosisRecord.revision.label("revision"),
            literal(None).label("reused_from_diagnosis_id"),
            IncidentHumanDiagnosisRecord.created_at.label("created_at"),
        )
        .join(IncidentRecord, IncidentRecord.id == IncidentHumanDiagnosisRecord.incident_id)
        .join(
            latest_human_revision,
            (IncidentHumanDiagnosisRecord.incident_id == latest_human_revision.c.incident_id)
            & (IncidentHumanDiagnosisRecord.revision == latest_human_revision.c.revision),
        )
        .where(*human_filters)
    )
    history = union_all(automatic, human).subquery()
    total = session.scalar(select(func.count()).select_from(history)) or 0
    rows = (
        session.execute(
            select(history)
            .order_by(history.c.created_at.desc(), history.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .mappings()
        .all()
    )
    return DiagnosisHistoryResponse(
        items=[
            DiagnosisHistoryItemResponse(
                id=row["id"],
                incident_id=row["incident_id"],
                error_signature_id=row["error_signature_id"],
                source_type=DiagnosisHistorySource(row["source_type"]),
                root_cause=row["root_cause"],
                status=row["status"],
                actor_identity=row["actor_identity"],
                revision=row["revision"],
                reused_from_diagnosis_id=row["reused_from_diagnosis_id"],
                created_at=_as_utc(row["created_at"]),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{diagnosis_id}", response_model=DiagnosisDetailResponse)
def get_diagnosis(
    diagnosis_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> DiagnosisDetailResponse:
    """Return complete sanitized content and provenance for one Diagnosis."""
    diagnosis = session.get(DiagnosisRecord, diagnosis_id)
    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnosis not found",
        )
    context = _load_context(session, {diagnosis.failure_event_id})
    content = _content_record(diagnosis, context)
    summary = _summary_response(diagnosis, context)
    failure = session.get(FailureEventRecord, diagnosis.failure_event_id)
    if failure is None:  # pragma: no cover - database invariant
        raise RuntimeError("Diagnosis has no Failure Event")
    incident_id = session.scalar(
        select(IncidentFailureRecord.incident_id).where(
            IncidentFailureRecord.failure_event_id == failure.id
        )
    )
    signature = (
        session.get(ErrorSignatureRecord, diagnosis.error_signature_id)
        if diagnosis.error_signature_id is not None
        else None
    )
    effective = context.effective_by_failure.get(diagnosis.failure_event_id)
    delivery = context.deliveries_by_diagnosis.get(effective.id) if effective is not None else None
    airflow_log_url = delivery.payload.get("airflow_log_url") if delivery is not None else None
    if not isinstance(airflow_log_url, str) or not airflow_log_url:
        airflow_log_url = None
    return DiagnosisDetailResponse(
        **summary.model_dump(),
        confidence_reason=content.confidence_reason,
        matched_rule=content.matched_rule,
        extracted_values=[
            DiagnosisExtractedValueResponse.model_validate(value)
            for value in content.extracted_values or []
        ],
        evidence=[
            DiagnosisEvidenceResponse.model_validate(item) for item in content.evidence or []
        ],
        recommended_actions=list(content.recommended_actions or []),
        retry_decision=content.retry_decision,
        validation_errors=list(diagnosis.validation_errors or []),
        failure=DiagnosisFailureResponse(
            id=failure.id,
            environment=failure.environment,
            dag_id=failure.dag_id,
            dag_run_id=failure.dag_run_id,
            task_id=failure.task_id,
            map_index=failure.map_index,
            try_number=failure.try_number,
            state=failure.state,
            observed_at=_as_utc(failure.observed_at),
        ),
        incident_id=incident_id,
        error_signature=(_signature_response(signature) if signature is not None else None),
        airflow_log_url=airflow_log_url,
    )


def _load_context(session: Session, failure_ids: set[UUID]) -> DiagnosisQueryContext:
    if not failure_ids:
        return DiagnosisQueryContext({}, {}, {})
    diagnoses = session.scalars(
        select(DiagnosisRecord)
        .where(DiagnosisRecord.failure_event_id.in_(failure_ids))
        .order_by(DiagnosisRecord.created_at, DiagnosisRecord.id)
    ).all()
    diagnoses_by_failure: dict[UUID, list[DiagnosisRecord]] = {}
    for diagnosis in diagnoses:
        diagnoses_by_failure.setdefault(diagnosis.failure_event_id, []).append(diagnosis)

    diagnosis_ids = [diagnosis.id for diagnosis in diagnoses]
    deliveries = session.scalars(
        select(NotificationDeliveryRecord).where(
            NotificationDeliveryRecord.diagnosis_id.in_(diagnosis_ids)
        )
    ).all()
    deliveries_by_diagnosis = {delivery.diagnosis_id: delivery for delivery in deliveries}
    content_by_id = {diagnosis.id: diagnosis for diagnosis in diagnoses}
    reused_from_ids = {
        diagnosis.reused_from_diagnosis_id
        for diagnosis in diagnoses
        if diagnosis.reused_from_diagnosis_id is not None
    }
    missing_content_ids = reused_from_ids - content_by_id.keys()
    if missing_content_ids:
        originals = session.scalars(
            select(DiagnosisRecord).where(DiagnosisRecord.id.in_(missing_content_ids))
        ).all()
        content_by_id.update({original.id: original for original in originals})

    effective_by_failure: dict[UUID, DiagnosisRecord] = {}
    for failure_id, failure_diagnoses in diagnoses_by_failure.items():
        effective = next(
            (
                diagnosis
                for diagnosis in reversed(failure_diagnoses)
                if diagnosis.id in deliveries_by_diagnosis
            ),
            None,
        )
        if effective is None:
            effective = next(
                (
                    diagnosis
                    for diagnosis in reversed(failure_diagnoses)
                    if diagnosis.validation_status == DiagnosisValidationStatus.PASSED
                ),
                None,
            )
        if effective is not None:
            effective_by_failure[failure_id] = effective
    return DiagnosisQueryContext(
        content_by_id=content_by_id,
        effective_by_failure=effective_by_failure,
        deliveries_by_diagnosis=deliveries_by_diagnosis,
    )


def _summary_response(
    diagnosis: DiagnosisRecord,
    context: DiagnosisQueryContext,
) -> DiagnosisSummaryResponse:
    content = _content_record(diagnosis, context)
    effective = context.effective_by_failure.get(diagnosis.failure_event_id)
    return DiagnosisSummaryResponse(
        id=diagnosis.id,
        failure_event_id=diagnosis.failure_event_id,
        error_signature_id=diagnosis.error_signature_id,
        effective=effective is not None and diagnosis.id == effective.id,
        effective_diagnosis_id=effective.id if effective is not None else None,
        source=diagnosis.source,
        validation_status=diagnosis.validation_status,
        content_diagnosis_id=content.id,
        reused_from_diagnosis_id=diagnosis.reused_from_diagnosis_id,
        classification=content.classification,
        root_cause=content.root_cause,
        confidence=content.confidence,
        operator_review_required=content.operator_review_required,
        diagnosis_schema_version=diagnosis.diagnosis_schema_version,
        prompt_version=diagnosis.prompt_version,
        rule_version=diagnosis.rule_version,
        created_at=_as_utc(diagnosis.created_at),
    )


def _content_record(
    diagnosis: DiagnosisRecord,
    context: DiagnosisQueryContext,
) -> DiagnosisRecord:
    if diagnosis.reused_from_diagnosis_id is None:
        return diagnosis
    return context.content_by_id.get(diagnosis.reused_from_diagnosis_id, diagnosis)


def _signature_response(record: ErrorSignatureRecord) -> DiagnosisErrorSignatureResponse:
    return DiagnosisErrorSignatureResponse(
        id=record.id,
        fingerprint_version=record.fingerprint_version,
        fingerprint=record.fingerprint,
        operator_type=record.operator_type,
        exception_class=record.exception_class,
        vendor_error_code=record.vendor_error_code,
        normalized_message=record.normalized_message,
        application_stack_frame=record.application_stack_frame,
    )


def _validate_date_order(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to",
        )


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
