"""Authenticated Error Signature exploration routes."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
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
from dagsentry.domain.incident import IncidentStatus
from dagsentry.models import (
    DiagnosisRecord,
    ErrorSignatureRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentHumanDiagnosisRecord,
    IncidentRecord,
)
from dagsentry.security import authenticate_query_principal

router = APIRouter(
    prefix="/api/v1/error-signatures",
    tags=["error-signatures"],
    dependencies=[Depends(authenticate_query_principal)],
)


class ErrorSignatureSummaryResponse(BaseModel):
    """Canonical Signature fields plus occurrence statistics."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    fingerprint_version: int
    fingerprint: str
    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None
    failure_count: int
    incident_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime


class ErrorSignatureListResponse(BaseModel):
    """Bounded Error Signature list."""

    model_config = ConfigDict(extra="forbid")

    items: list[ErrorSignatureSummaryResponse]
    total: int
    limit: int
    offset: int


class LatestValidatedDiagnosisResponse(BaseModel):
    """Latest validated original Diagnosis for one Signature."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: DiagnosisSource
    classification: ErrorClassification
    root_cause: str | None
    confidence: float
    retry_decision: RetryDecision
    diagnosis_schema_version: int
    prompt_version: str | None
    rule_version: int | None
    created_at: datetime


class ErrorSignatureDetailResponse(ErrorSignatureSummaryResponse):
    """One Signature with its latest validated Diagnosis summary."""

    latest_validated_diagnosis: LatestValidatedDiagnosisResponse | None
    operator_diagnoses: list[SignatureOperatorDiagnosisResponse]


class SignatureOperatorDiagnosisResponse(BaseModel):
    """Latest published operator diagnosis for one past Incident of this Signature."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    incident_id: UUID
    revision: int
    classification: ErrorClassification
    retry_decision: RetryDecision
    root_cause: str
    recommended_actions: list[str]
    actor_identity: str
    created_at: datetime


class SignatureOccurrenceResponse(BaseModel):
    """One exact Failure occurrence of an Error Signature."""

    model_config = ConfigDict(extra="forbid")

    failure_event_id: UUID
    incident_id: UUID
    environment: str
    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    failure_state: FailureState
    observed_at: datetime
    incident_status: IncidentStatus


class SignatureOccurrenceListResponse(BaseModel):
    """Bounded chronological occurrences for one Signature."""

    model_config = ConfigDict(extra="forbid")

    items: list[SignatureOccurrenceResponse]
    total: int
    limit: int
    offset: int


class SignatureTrendBucketResponse(BaseModel):
    """One UTC day and its Failure count."""

    model_config = ConfigDict(extra="forbid")

    date: date
    failure_count: int


class SignatureTrendResponse(BaseModel):
    """Zero-filled UTC daily occurrence trend."""

    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    bucket: str
    items: list[SignatureTrendBucketResponse]


class ErrorSignatureSort(StrEnum):
    """Allowlisted Error Signature list sort fields."""

    LAST_SEEN_AT = "last_seen_at"
    FAILURE_COUNT = "failure_count"
    INCIDENT_COUNT = "incident_count"
    CREATED_AT = "created_at"


class SortOrder(StrEnum):
    """Stable list ordering directions."""

    ASC = "asc"
    DESC = "desc"


@router.get("", response_model=ErrorSignatureListResponse)
def list_error_signatures(
    session: Annotated[Session, Depends(get_session)],
    environment: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    dag_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    task_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    classification: ErrorClassification | None = None,
    query: Annotated[str | None, Query(alias="q", min_length=1, max_length=250)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: Annotated[ErrorSignatureSort, Query(alias="sort")] = (ErrorSignatureSort.LAST_SEEN_AT),
    order: SortOrder = SortOrder.DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ErrorSignatureListResponse:
    """List recurring Error Signatures using bounded operational filters."""
    _validate_date_order(date_from, date_to)
    occurrence_filters = _occurrence_filters(
        environment=environment,
        dag_id=dag_id,
        task_id=task_id,
        date_from=date_from,
        date_to=date_to,
    )
    stats = (
        select(
            IncidentRecord.error_signature_id.label("error_signature_id"),
            func.count(FailureEventRecord.id).label("failure_count"),
            func.count(func.distinct(IncidentRecord.id)).label("incident_count"),
            func.min(FailureEventRecord.observed_at).label("first_seen_at"),
            func.max(FailureEventRecord.observed_at).label("last_seen_at"),
        )
        .select_from(IncidentRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.incident_id == IncidentRecord.id,
        )
        .join(
            FailureEventRecord,
            FailureEventRecord.id == IncidentFailureRecord.failure_event_id,
        )
        .where(IncidentRecord.error_signature_id.is_not(None), *occurrence_filters)
        .group_by(IncidentRecord.error_signature_id)
        .subquery()
    )
    signature_filters = _signature_filters(classification=classification, query=query)
    sort_columns = {
        ErrorSignatureSort.LAST_SEEN_AT: stats.c.last_seen_at,
        ErrorSignatureSort.FAILURE_COUNT: stats.c.failure_count,
        ErrorSignatureSort.INCIDENT_COUNT: stats.c.incident_count,
        ErrorSignatureSort.CREATED_AT: ErrorSignatureRecord.created_at,
    }
    sort_column = sort_columns[sort_by]
    primary_order = sort_column.asc() if order == SortOrder.ASC else sort_column.desc()
    stable_order = (
        ErrorSignatureRecord.id.asc() if order == SortOrder.ASC else ErrorSignatureRecord.id.desc()
    )
    rows = session.execute(
        select(
            ErrorSignatureRecord,
            stats.c.failure_count,
            stats.c.incident_count,
            stats.c.first_seen_at,
            stats.c.last_seen_at,
        )
        .join(stats, stats.c.error_signature_id == ErrorSignatureRecord.id)
        .where(*signature_filters)
        .order_by(primary_order, stable_order)
        .limit(limit)
        .offset(offset)
    ).all()
    total = (
        session.scalar(
            select(func.count())
            .select_from(ErrorSignatureRecord)
            .join(stats, stats.c.error_signature_id == ErrorSignatureRecord.id)
            .where(*signature_filters)
        )
        or 0
    )
    return ErrorSignatureListResponse(
        items=[
            _signature_summary(
                record,
                failure_count=int(failure_count),
                incident_count=int(incident_count),
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
            )
            for record, failure_count, incident_count, first_seen_at, last_seen_at in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{signature_id}", response_model=ErrorSignatureDetailResponse)
def get_error_signature(
    signature_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ErrorSignatureDetailResponse:
    """Return one canonical Signature and its latest validated original Diagnosis."""
    signature = _get_signature(session, signature_id)
    failure_count, incident_count, first_seen_at, last_seen_at = _signature_statistics(
        session, signature_id
    )
    latest = session.scalar(
        select(DiagnosisRecord)
        .where(
            DiagnosisRecord.error_signature_id == signature_id,
            DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
            DiagnosisRecord.source != DiagnosisSource.REUSED,
        )
        .order_by(DiagnosisRecord.created_at.desc(), DiagnosisRecord.id.desc())
        .limit(1)
    )
    summary = _signature_summary(
        signature,
        failure_count=failure_count,
        incident_count=incident_count,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
    )
    latest_human_revision = (
        select(
            IncidentHumanDiagnosisRecord.incident_id.label("incident_id"),
            func.max(IncidentHumanDiagnosisRecord.revision).label("revision"),
        )
        .join(IncidentRecord, IncidentRecord.id == IncidentHumanDiagnosisRecord.incident_id)
        .where(IncidentRecord.error_signature_id == signature_id)
        .group_by(IncidentHumanDiagnosisRecord.incident_id)
        .subquery()
    )
    operator_diagnoses = session.scalars(
        select(IncidentHumanDiagnosisRecord)
        .join(
            latest_human_revision,
            (IncidentHumanDiagnosisRecord.incident_id == latest_human_revision.c.incident_id)
            & (IncidentHumanDiagnosisRecord.revision == latest_human_revision.c.revision),
        )
        .where(IncidentHumanDiagnosisRecord.action == HumanDiagnosisAction.PUBLISH)
        .order_by(
            IncidentHumanDiagnosisRecord.created_at.desc(),
            IncidentHumanDiagnosisRecord.id.desc(),
        )
        .limit(10)
    ).all()
    return ErrorSignatureDetailResponse(
        **summary.model_dump(),
        latest_validated_diagnosis=(
            _latest_diagnosis_response(latest) if latest is not None else None
        ),
        operator_diagnoses=[
            SignatureOperatorDiagnosisResponse(
                id=record.id,
                incident_id=record.incident_id,
                revision=record.revision,
                classification=record.classification or ErrorClassification.UNKNOWN,
                retry_decision=record.retry_decision or RetryDecision.UNKNOWN,
                root_cause=record.root_cause or "",
                recommended_actions=list(record.recommended_actions or []),
                actor_identity=record.actor_identity,
                created_at=_as_utc(record.created_at),
            )
            for record in operator_diagnoses
        ],
    )


@router.get("/{signature_id}/occurrences", response_model=SignatureOccurrenceListResponse)
def list_signature_occurrences(
    signature_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    environment: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    dag_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    task_id: Annotated[str | None, Query(min_length=1, max_length=250)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SignatureOccurrenceListResponse:
    """List exact Failure and Incident occurrences for one Signature."""
    _validate_date_order(date_from, date_to)
    _get_signature(session, signature_id)
    filters = _occurrence_filters(
        environment=environment,
        dag_id=dag_id,
        task_id=task_id,
        date_from=date_from,
        date_to=date_to,
    )
    base = (
        select(IncidentRecord, FailureEventRecord)
        .select_from(IncidentRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.incident_id == IncidentRecord.id,
        )
        .join(
            FailureEventRecord,
            FailureEventRecord.id == IncidentFailureRecord.failure_event_id,
        )
        .where(IncidentRecord.error_signature_id == signature_id, *filters)
    )
    rows = session.execute(
        base.order_by(FailureEventRecord.observed_at.desc(), FailureEventRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    return SignatureOccurrenceListResponse(
        items=[
            SignatureOccurrenceResponse(
                failure_event_id=failure.id,
                incident_id=incident.id,
                environment=incident.environment,
                dag_id=incident.dag_id,
                dag_run_id=failure.dag_run_id,
                task_id=incident.task_id,
                map_index=failure.map_index,
                try_number=failure.try_number,
                failure_state=failure.state,
                observed_at=_as_utc(failure.observed_at),
                incident_status=incident.status,
            )
            for incident, failure in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{signature_id}/trend", response_model=SignatureTrendResponse)
def get_signature_trend(
    signature_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    date_from: date,
    date_to: date,
) -> SignatureTrendResponse:
    """Return a zero-filled UTC daily Failure trend over at most 366 days."""
    _validate_trend_range(date_from, date_to)
    _get_signature(session, signature_id)
    bucket = func.date(FailureEventRecord.observed_at)
    rows = session.execute(
        select(bucket, func.count(FailureEventRecord.id))
        .select_from(IncidentRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.incident_id == IncidentRecord.id,
        )
        .join(
            FailureEventRecord,
            FailureEventRecord.id == IncidentFailureRecord.failure_event_id,
        )
        .where(
            IncidentRecord.error_signature_id == signature_id,
            FailureEventRecord.observed_at >= _day_start(date_from),
            FailureEventRecord.observed_at < _day_start(date_to + timedelta(days=1)),
        )
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    counts = {_coerce_date(bucket_date): int(count) for bucket_date, count in rows}
    days = (date_to - date_from).days
    return SignatureTrendResponse(
        date_from=date_from,
        date_to=date_to,
        bucket="day",
        items=[
            SignatureTrendBucketResponse(
                date=date_from + timedelta(days=offset),
                failure_count=counts.get(date_from + timedelta(days=offset), 0),
            )
            for offset in range(days + 1)
        ],
    )


def _signature_filters(
    *,
    classification: ErrorClassification | None,
    query: str | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if classification is not None:
        filters.append(
            select(DiagnosisRecord.id)
            .where(
                DiagnosisRecord.error_signature_id == ErrorSignatureRecord.id,
                DiagnosisRecord.classification == classification,
            )
            .exists()
        )
    if query is not None:
        pattern = f"%{query}%"
        filters.append(
            or_(
                ErrorSignatureRecord.normalized_message.ilike(pattern),
                ErrorSignatureRecord.exception_class.ilike(pattern),
                ErrorSignatureRecord.vendor_error_code.ilike(pattern),
                ErrorSignatureRecord.operator_type.ilike(pattern),
            )
        )
    return filters


def _occurrence_filters(
    *,
    environment: str | None,
    dag_id: str | None,
    task_id: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if environment is not None:
        filters.append(IncidentRecord.environment == environment)
    if dag_id is not None:
        filters.append(IncidentRecord.dag_id == dag_id)
    if task_id is not None:
        filters.append(IncidentRecord.task_id == task_id)
    if date_from is not None:
        filters.append(FailureEventRecord.observed_at >= _day_start(date_from))
    if date_to is not None:
        filters.append(FailureEventRecord.observed_at < _day_start(date_to + timedelta(days=1)))
    return filters


def _signature_statistics(
    session: Session,
    signature_id: UUID,
) -> tuple[int, int, datetime | None, datetime | None]:
    row = session.execute(
        select(
            func.count(FailureEventRecord.id),
            func.count(func.distinct(IncidentRecord.id)),
            func.min(FailureEventRecord.observed_at),
            func.max(FailureEventRecord.observed_at),
        )
        .select_from(IncidentRecord)
        .join(
            IncidentFailureRecord,
            IncidentFailureRecord.incident_id == IncidentRecord.id,
        )
        .join(
            FailureEventRecord,
            FailureEventRecord.id == IncidentFailureRecord.failure_event_id,
        )
        .where(IncidentRecord.error_signature_id == signature_id)
    ).one()
    return int(row[0]), int(row[1]), row[2], row[3]


def _signature_summary(
    record: ErrorSignatureRecord,
    *,
    failure_count: int,
    incident_count: int,
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
) -> ErrorSignatureSummaryResponse:
    return ErrorSignatureSummaryResponse(
        id=record.id,
        fingerprint_version=record.fingerprint_version,
        fingerprint=record.fingerprint,
        operator_type=record.operator_type,
        exception_class=record.exception_class,
        vendor_error_code=record.vendor_error_code,
        normalized_message=record.normalized_message,
        application_stack_frame=record.application_stack_frame,
        failure_count=failure_count,
        incident_count=incident_count,
        first_seen_at=_as_utc(first_seen_at) if first_seen_at is not None else None,
        last_seen_at=_as_utc(last_seen_at) if last_seen_at is not None else None,
        created_at=_as_utc(record.created_at),
    )


def _latest_diagnosis_response(record: DiagnosisRecord) -> LatestValidatedDiagnosisResponse:
    assert record.classification is not None
    assert record.confidence is not None
    assert record.retry_decision is not None
    return LatestValidatedDiagnosisResponse(
        id=record.id,
        source=record.source,
        classification=record.classification,
        root_cause=record.root_cause,
        confidence=record.confidence,
        retry_decision=record.retry_decision,
        diagnosis_schema_version=record.diagnosis_schema_version,
        prompt_version=record.prompt_version,
        rule_version=record.rule_version,
        created_at=_as_utc(record.created_at),
    )


def _get_signature(session: Session, signature_id: UUID) -> ErrorSignatureRecord:
    signature = session.get(ErrorSignatureRecord, signature_id)
    if signature is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Error Signature not found",
        )
    return signature


def _validate_date_order(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to",
        )


def _validate_trend_range(date_from: date, date_to: date) -> None:
    _validate_date_order(date_from, date_to)
    if (date_to - date_from).days > 365:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="trend range must not exceed 366 days",
        )


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
