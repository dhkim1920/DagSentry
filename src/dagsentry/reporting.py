"""Reproducible SQL aggregation for one environment and UTC report date."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, select
from sqlalchemy.orm import Session, aliased

from dagsentry.domain.diagnosis import DiagnosisValidationStatus, ErrorClassification
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.reporting import (
    DailyStatistics,
    ErrorSignatureStatistics,
    IncidentStatistics,
    MeanTimeStatistics,
    TopFailure,
    report_period,
)
from dagsentry.models import (
    DiagnosisRecord,
    FailureEventRecord,
    IncidentFailureRecord,
    IncidentRecord,
    IncidentStateTransitionRecord,
)


def aggregate_daily_statistics(
    session: Session,
    *,
    report_date: date,
    environment: str,
    timezone: str = "UTC",
) -> DailyStatistics:
    """Aggregate a stable UTC half-open day using SQL-derived values only."""
    period_start, period_end = report_period(report_date, timezone)
    failure_filter = (
        FailureEventRecord.environment == environment,
        FailureEventRecord.observed_at >= period_start,
        FailureEventRecord.observed_at < period_end,
    )

    failure_attempts = _count(session, select(func.count()).where(*failure_filter))
    task_instances = (
        select(
            FailureEventRecord.dag_id,
            FailureEventRecord.dag_run_id,
            FailureEventRecord.task_id,
            FailureEventRecord.map_index,
        )
        .where(*failure_filter)
        .distinct()
        .subquery()
    )
    affected_task_instances = _count(
        session,
        select(func.count()).select_from(task_instances),
    )
    dag_runs = (
        select(FailureEventRecord.dag_id, FailureEventRecord.dag_run_id)
        .where(*failure_filter)
        .distinct()
        .subquery()
    )
    affected_dag_runs = _count(session, select(func.count()).select_from(dag_runs))

    terminal_transition_before_end = exists(
        select(IncidentStateTransitionRecord.id).where(
            IncidentStateTransitionRecord.incident_id == IncidentRecord.id,
            IncidentStateTransitionRecord.created_at < period_end,
            IncidentStateTransitionRecord.status.in_(
                {
                    IncidentStatus.RECOVERED,
                    IncidentStatus.RESOLVED,
                    IncidentStatus.IGNORED,
                }
            ),
        )
    )
    new_incidents = _count(
        session,
        select(func.count()).where(
            IncidentRecord.environment == environment,
            IncidentRecord.created_at >= period_start,
            IncidentRecord.created_at < period_end,
        ),
    )
    unresolved_incidents = _count(
        session,
        select(func.count()).where(
            IncidentRecord.environment == environment,
            IncidentRecord.created_at < period_end,
            ~terminal_transition_before_end,
        ),
    )
    recovered_incidents = _transition_count(
        session,
        environment=environment,
        status=IncidentStatus.RECOVERED,
        period_start=period_start,
        period_end=period_end,
    )

    effective_diagnoses = (
        select(
            DiagnosisRecord.failure_event_id.label("failure_event_id"),
            DiagnosisRecord.error_signature_id.label("error_signature_id"),
            DiagnosisRecord.classification.label("classification"),
            DiagnosisRecord.reused_from_diagnosis_id.label("reused_from_diagnosis_id"),
            func.row_number()
            .over(
                partition_by=DiagnosisRecord.failure_event_id,
                order_by=(DiagnosisRecord.created_at.desc(), DiagnosisRecord.id.desc()),
            )
            .label("diagnosis_rank"),
        )
        .where(DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED)
        .subquery()
    )
    current_signatures = (
        select(effective_diagnoses.c.error_signature_id.label("signature_id"))
        .join(
            FailureEventRecord,
            FailureEventRecord.id == effective_diagnoses.c.failure_event_id,
        )
        .where(
            *failure_filter,
            effective_diagnoses.c.diagnosis_rank == 1,
            effective_diagnoses.c.error_signature_id.is_not(None),
        )
        .distinct()
        .subquery()
    )
    prior_signatures = (
        select(effective_diagnoses.c.error_signature_id.label("signature_id"))
        .join(
            FailureEventRecord,
            FailureEventRecord.id == effective_diagnoses.c.failure_event_id,
        )
        .where(
            FailureEventRecord.environment == environment,
            FailureEventRecord.observed_at < period_start,
            effective_diagnoses.c.diagnosis_rank == 1,
            effective_diagnoses.c.error_signature_id.is_not(None),
        )
        .distinct()
        .subquery()
    )
    new_signatures = _count(
        session,
        select(func.count())
        .select_from(
            current_signatures.outerjoin(
                prior_signatures,
                prior_signatures.c.signature_id == current_signatures.c.signature_id,
            )
        )
        .where(prior_signatures.c.signature_id.is_(None)),
    )
    repeated_signatures = _count(
        session,
        select(func.count()).select_from(
            current_signatures.join(
                prior_signatures,
                prior_signatures.c.signature_id == current_signatures.c.signature_id,
            )
        ),
    )

    original = aliased(DiagnosisRecord)
    effective_classification = func.coalesce(
        original.classification,
        effective_diagnoses.c.classification,
    )
    classification_rows = session.execute(
        select(effective_classification, func.count(func.distinct(FailureEventRecord.id)))
        .select_from(effective_diagnoses)
        .join(
            FailureEventRecord,
            FailureEventRecord.id == effective_diagnoses.c.failure_event_id,
        )
        .outerjoin(
            original,
            original.id == effective_diagnoses.c.reused_from_diagnosis_id,
        )
        .where(
            *failure_filter,
            effective_diagnoses.c.diagnosis_rank == 1,
            effective_classification.is_not(None),
        )
        .group_by(effective_classification)
    ).tuples()
    classification_counts = {classification: 0 for classification in ErrorClassification}
    for classification, count in classification_rows:
        classification_counts[ErrorClassification(classification)] = count

    return DailyStatistics(
        report_date=report_date,
        timezone=timezone,
        period_start=period_start,
        period_end=period_end,
        environment=environment,
        failure_attempts=failure_attempts,
        affected_task_instances=affected_task_instances,
        affected_dag_runs=affected_dag_runs,
        incidents=IncidentStatistics(
            new=new_incidents,
            unresolved=unresolved_incidents,
            recovered=recovered_incidents,
        ),
        error_signatures=ErrorSignatureStatistics(
            new=new_signatures,
            repeated=repeated_signatures,
        ),
        classification_counts=classification_counts,
        top_failures=_top_failures(session, failure_filter),
        mean_time=MeanTimeStatistics(
            recovery_seconds=_mean_transition_seconds(
                session,
                environment=environment,
                status=IncidentStatus.RECOVERED,
                period_start=period_start,
                period_end=period_end,
            ),
            resolution_seconds=_mean_transition_seconds(
                session,
                environment=environment,
                status=IncidentStatus.RESOLVED,
                period_start=period_start,
                period_end=period_end,
            ),
        ),
    )


def _top_failures(
    session: Session, failure_filter: tuple[ColumnElement[bool], ...]
) -> tuple[TopFailure, ...]:
    counts = session.execute(
        select(
            FailureEventRecord.dag_id,
            FailureEventRecord.task_id,
            func.count().label("failure_count"),
            func.max(FailureEventRecord.observed_at),
        )
        .where(*failure_filter)
        .group_by(FailureEventRecord.dag_id, FailureEventRecord.task_id)
        .order_by(func.count().desc(), FailureEventRecord.dag_id, FailureEventRecord.task_id)
        .limit(20)
    ).all()
    result: list[TopFailure] = []
    for dag_id, task_id, count, last_failed in counts:
        latest = session.execute(
            select(DiagnosisRecord, IncidentRecord)
            .join(FailureEventRecord, FailureEventRecord.id == DiagnosisRecord.failure_event_id)
            .outerjoin(
                IncidentFailureRecord,
                IncidentFailureRecord.failure_event_id == FailureEventRecord.id,
            )
            .outerjoin(IncidentRecord, IncidentRecord.id == IncidentFailureRecord.incident_id)
            .where(
                *failure_filter,
                FailureEventRecord.dag_id == dag_id,
                FailureEventRecord.task_id == task_id,
                DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
            )
            .order_by(
                FailureEventRecord.observed_at.desc(),
                FailureEventRecord.id.desc(),
                DiagnosisRecord.created_at.desc(),
                DiagnosisRecord.id.desc(),
            )
            .limit(1)
        ).first()
        diagnosis, incident = latest if latest is not None else (None, None)
        if diagnosis is not None and diagnosis.reused_from_diagnosis_id is not None:
            diagnosis = session.get(DiagnosisRecord, diagnosis.reused_from_diagnosis_id)
        result.append(
            TopFailure(
                dag_id=dag_id,
                task_id=task_id,
                failure_count=count,
                last_failed_at=last_failed.replace(tzinfo=UTC)
                if last_failed.tzinfo is None
                else last_failed.astimezone(UTC),
                classification=diagnosis.classification if diagnosis is not None else None,
                root_cause=diagnosis.root_cause if diagnosis is not None else None,
                incident_id=incident.id if incident is not None else None,
                incident_status=incident.status if incident is not None else None,
            )
        )
    return tuple(result)


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    value = session.scalar(statement)
    return int(value or 0)


def _transition_count(
    session: Session,
    *,
    environment: str,
    status: IncidentStatus,
    period_start: datetime,
    period_end: datetime,
) -> int:
    return _count(
        session,
        select(func.count(func.distinct(IncidentStateTransitionRecord.incident_id)))
        .join(IncidentRecord, IncidentRecord.id == IncidentStateTransitionRecord.incident_id)
        .where(
            IncidentRecord.environment == environment,
            IncidentStateTransitionRecord.status == status,
            IncidentStateTransitionRecord.created_at >= period_start,
            IncidentStateTransitionRecord.created_at < period_end,
        ),
    )


def _mean_transition_seconds(
    session: Session,
    *,
    environment: str,
    status: IncidentStatus,
    period_start: datetime,
    period_end: datetime,
) -> float | None:
    dialect = session.get_bind().dialect.name
    duration_seconds: ColumnElement[Any]
    if dialect == "postgresql":
        duration_seconds = func.extract(
            "epoch",
            IncidentStateTransitionRecord.created_at - IncidentRecord.created_at,
        )
    elif dialect == "sqlite":
        duration_seconds = (
            func.julianday(IncidentStateTransitionRecord.created_at)
            - func.julianday(IncidentRecord.created_at)
        ) * 86_400.0
    else:  # pragma: no cover - only configured dialects are supported
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    value = session.scalar(
        select(func.avg(duration_seconds))
        .join(IncidentRecord, IncidentRecord.id == IncidentStateTransitionRecord.incident_id)
        .where(
            IncidentRecord.environment == environment,
            IncidentStateTransitionRecord.status == status,
            IncidentStateTransitionRecord.created_at >= period_start,
            IncidentStateTransitionRecord.created_at < period_end,
        )
    )
    return float(value) if value is not None else None
