"""Admin-only service observations and Diagnosis queue snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dagsentry import __version__
from dagsentry.config import Settings
from dagsentry.db import get_session
from dagsentry.models import (
    DailyReportScheduleRunRecord,
    DailyReportScheduleRunStatus,
    DiagnosisOutboxRecord,
    OutboxStatus,
    SchedulerHeartbeatRecord,
    WorkerHeartbeatRecord,
)
from dagsentry.security import require_admin

router = APIRouter(
    prefix="/api/v1/admin/operations",
    tags=["operations"],
    dependencies=[Depends(require_admin)],
)


class ServiceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ONLINE", "OFFLINE", "UNKNOWN"]
    last_heartbeat_at: datetime | None
    last_success_at: datetime | None


class WorkerObservation(ServiceObservation):
    online_instances: int


class QueueSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int
    ready: int
    delayed: int
    processing: int
    stale_processing: int
    dead: int
    completed: int
    oldest_ready_at: datetime | None
    oldest_ready_age_seconds: float


class OperationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    version: str
    api_status: Literal["ONLINE"] = "ONLINE"
    database_status: Literal["READY"] = "READY"
    worker: WorkerObservation
    scheduler: ServiceObservation
    queue: QueueSnapshot


@router.get("", response_model=OperationsResponse)
def get_operations(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> OperationsResponse:
    """Return current global observations without raw errors, machine names or secrets."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return _snapshot(session, request.app.state.settings, datetime.now(UTC))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Operational status is unavailable",
            headers={"Cache-Control": "no-store"},
        ) from error


def _snapshot(session: Session, settings: Settings, now: datetime) -> OperationsResponse:
    pending_filter = DiagnosisOutboxRecord.status == OutboxStatus.PENDING
    ready_filter = pending_filter & (DiagnosisOutboxRecord.available_at <= now)
    processing_filter = DiagnosisOutboxRecord.status == OutboxStatus.PROCESSING
    completed_filter = DiagnosisOutboxRecord.status == OutboxStatus.COMPLETED
    # Derive related counts from one SQL snapshot, including concurrent job transitions.
    pending, ready, processing, stale_processing, dead, completed, oldest_ready, last_completed = (
        session.execute(
            select(
                func.count(case((pending_filter, 1))),
                func.count(case((ready_filter, 1))),
                func.count(case((processing_filter, 1))),
                func.count(
                    case(
                        (
                            processing_filter
                            & (
                                DiagnosisOutboxRecord.locked_at
                                <= now
                                - timedelta(seconds=settings.worker_stale_lock_timeout_seconds)
                            ),
                            1,
                        )
                    )
                ),
                func.count(case((DiagnosisOutboxRecord.status == OutboxStatus.DEAD, 1))),
                func.count(case((completed_filter, 1))),
                func.min(case((ready_filter, DiagnosisOutboxRecord.available_at))),
                func.max(case((completed_filter, DiagnosisOutboxRecord.updated_at))),
            )
        ).one()
    )
    worker_count, online_count, last_worker_heartbeat = session.execute(
        select(
            func.count(),
            func.count(
                case(
                    (
                        WorkerHeartbeatRecord.stopped_at.is_(None)
                        & (
                            WorkerHeartbeatRecord.last_heartbeat_at
                            >= now - timedelta(seconds=settings.worker_offline_after_seconds)
                        ),
                        1,
                    )
                )
            ),
            func.max(WorkerHeartbeatRecord.last_heartbeat_at),
        )
    ).one()
    scheduler = session.get(SchedulerHeartbeatRecord, "daily-report")
    last_report_success = session.scalar(
        select(func.max(DailyReportScheduleRunRecord.finished_at)).where(
            DailyReportScheduleRunRecord.status == DailyReportScheduleRunStatus.SUCCEEDED,
        )
    )
    scheduler_heartbeat = _utc(scheduler.last_heartbeat_at) if scheduler else None
    scheduler_status: Literal["ONLINE", "OFFLINE", "UNKNOWN"] = "UNKNOWN"
    if scheduler_heartbeat is not None:
        scheduler_status = (
            "ONLINE"
            if (now - scheduler_heartbeat).total_seconds()
            <= settings.scheduler_offline_after_seconds
            else "OFFLINE"
        )
    oldest_ready_at = _utc(oldest_ready) if oldest_ready is not None else None
    return OperationsResponse(
        observed_at=now,
        version=__version__,
        worker=WorkerObservation(
            status="ONLINE" if online_count else "OFFLINE" if worker_count else "UNKNOWN",
            online_instances=online_count,
            last_heartbeat_at=_utc(last_worker_heartbeat) if last_worker_heartbeat else None,
            last_success_at=_utc(last_completed) if last_completed else None,
        ),
        scheduler=ServiceObservation(
            status=scheduler_status,
            last_heartbeat_at=scheduler_heartbeat,
            last_success_at=_utc(last_report_success) if last_report_success else None,
        ),
        queue=QueueSnapshot(
            pending=pending,
            ready=ready,
            delayed=pending - ready,
            processing=processing,
            stale_processing=stale_processing,
            dead=dead,
            completed=completed,
            oldest_ready_at=oldest_ready_at,
            oldest_ready_age_seconds=max(0.0, (now - oldest_ready_at).total_seconds())
            if oldest_ready_at
            else 0,
        ),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
