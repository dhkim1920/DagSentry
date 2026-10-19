"""Admin inspection and explicit requeue of exhausted Diagnosis jobs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.db import get_session
from dagsentry.models import DiagnosisOutboxRecord, FailureEventRecord, OutboxStatus
from dagsentry.observability import correlation_id_context
from dagsentry.security import AuthenticatedPrincipal, require_admin
from dagsentry.worker_admin import (
    RequeuedWorkerJob,
    WorkerJobNotFoundError,
    WorkerJobStateError,
    requeue_dead_job,
)

router = APIRouter(
    prefix="/api/v1/admin/worker-jobs",
    tags=["worker-jobs"],
    dependencies=[Depends(require_admin)],
)


class DeadJobResponse(BaseModel):
    """Operational identifiers and error metadata, without the stored error text."""

    model_config = ConfigDict(extra="forbid")

    outbox_id: UUID
    failure_event_id: UUID
    environment: str
    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    attempt_count: int
    error_stage: str | None
    error_category: str | None
    error_type: str | None
    updated_at: datetime


class DeadJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeadJobResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=DeadJobListResponse)
def list_failed_worker_jobs(
    session: Annotated[Session, Depends(get_session)],
    environment: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeadJobListResponse:
    """List DEAD jobs oldest first, with their originating TaskInstance identity."""
    filters = [DiagnosisOutboxRecord.status == OutboxStatus.DEAD]
    if environment:
        filters.append(FailureEventRecord.environment == environment)
    statement = (
        select(DiagnosisOutboxRecord, FailureEventRecord)
        .join(FailureEventRecord, FailureEventRecord.id == DiagnosisOutboxRecord.failure_event_id)
        .where(*filters)
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(
        statement.order_by(DiagnosisOutboxRecord.updated_at, DiagnosisOutboxRecord.id)
        .limit(limit)
        .offset(offset)
    ).all()
    items = []
    for job, failure in rows:
        stage, category, error_type = _error_metadata(job.last_error)
        items.append(
            DeadJobResponse(
                outbox_id=job.id,
                failure_event_id=job.failure_event_id,
                environment=failure.environment,
                dag_id=failure.dag_id,
                dag_run_id=failure.dag_run_id,
                task_id=failure.task_id,
                map_index=failure.map_index,
                try_number=failure.try_number,
                attempt_count=job.attempt_count,
                error_stage=stage,
                error_category=category,
                error_type=error_type,
                updated_at=job.updated_at.replace(tzinfo=UTC)
                if job.updated_at.tzinfo is None
                else job.updated_at.astimezone(UTC),
            )
        )
    return DeadJobListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/{outbox_id}/requeue", response_model=RequeuedWorkerJob, status_code=202)
def requeue_worker_job(
    outbox_id: UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> RequeuedWorkerJob:
    """Atomically requeue a DEAD job and retain its Admin actor in audit history."""
    try:
        return requeue_dead_job(
            session,
            outbox_id=outbox_id,
            actor_user_id=principal.user_id,
            correlation_id=correlation_id_context.get(),
        )
    except WorkerJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkerJobStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is no longer failed. Refresh the list before retrying.",
        ) from error


def _error_metadata(value: str | None) -> tuple[str | None, str | None, str | None]:
    """Allow known stages/categories and a bounded exception class identifier only."""
    try:
        metadata = json.loads(value or "null")
    except (ValueError, TypeError):
        return None, None, None
    if not isinstance(metadata, dict):
        return None, None, None
    stage = metadata.get("stage")
    category = metadata.get("category")
    error_type = metadata.get("exception_type")
    return (
        stage
        if isinstance(stage, str)
        and stage
        in {
            "diagnosis",
            "load_event",
            "worker_recovery",
            "airflow_connection",
            "notification_connection",
            "llm_connection",
            "notification",
        }
        else None,
        category
        if isinstance(category, str)
        and category
        in {
            "attempts_exhausted",
            "permanent",
            "retryable",
            "stale_lock_reclaimed",
        }
        else None,
        error_type
        if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", error_type)
        else None,
    )
