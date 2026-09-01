"""Daily Report scheduler status and Admin configuration routes."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.config import Settings
from dagsentry.db import get_session
from dagsentry.domain.connection import ConnectionPurpose
from dagsentry.models import (
    AdminAuditEventRecord,
    DailyReportScheduleRecord,
    DailyReportScheduleRunRecord,
    DailyReportScheduleRunStatus,
    DailyReportScheduleRunTrigger,
    ManagedConnectionRecord,
    SchedulerHeartbeatRecord,
    utc_now,
)
from dagsentry.observability import correlation_id_context
from dagsentry.security import AuthenticatedPrincipal, authenticate_query_principal, require_admin

SCHEDULER_NAME = "daily-report"

router = APIRouter(
    prefix="/api/v1/daily-report-schedules",
    tags=["daily-report-schedules"],
    dependencies=[Depends(authenticate_query_principal)],
)
admin_router = APIRouter(
    prefix="/api/v1/admin/daily-report-schedules",
    tags=["daily-report-schedules"],
    dependencies=[Depends(require_admin)],
)


class ScheduleResponse(BaseModel):
    """Non-secret schedule settings and scheduler liveness."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    environment: str
    enabled: bool
    display_name: str
    report_title: str
    notification_connection_id: UUID | None
    use_ai_summary: bool
    run_at_local_time: time
    timezone: str
    revision: int
    applied_revision: int | None
    applied_at: datetime | None
    next_run_at: datetime | None
    scheduler_status: str
    last_heartbeat_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScheduleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScheduleResponse]


class SchedulerStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    last_heartbeat_at: datetime | None
    instance_id: UUID | None
    version: str | None


class ScheduleRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    schedule_id: UUID | None
    environment: str
    report_date: date
    trigger_type: DailyReportScheduleRunTrigger
    status: DailyReportScheduleRunStatus
    scheduled_for: datetime
    report_id: UUID | None
    error_category: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ScheduleRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScheduleRunResponse]
    total: int
    limit: int
    offset: int


class ScheduleWriteRequest(BaseModel):
    """Daily-only schedule input; arbitrary cron is intentionally unsupported."""

    model_config = ConfigDict(extra="forbid")

    environment: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    enabled: bool
    display_name: str = Field(min_length=1, max_length=120)
    report_title: str = Field(min_length=1, max_length=160)
    notification_connection_id: UUID | None = None
    use_ai_summary: bool = False
    run_at_local_time: time
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("run_at_local_time")
    @classmethod
    def reject_seconds(cls, value: time) -> time:
        if value.second or value.microsecond:
            raise ValueError("run_at_local_time must not include seconds")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class ScheduleUpdateRequest(ScheduleWriteRequest):
    expected_revision: int = Field(ge=1)


class ManualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_date: date


@router.get("", response_model=ScheduleListResponse)
def list_schedules(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> ScheduleListResponse:
    """Expose read-only schedule state to every authenticated UI role."""
    heartbeat = session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME)
    records = session.scalars(
        select(DailyReportScheduleRecord).order_by(DailyReportScheduleRecord.environment)
    ).all()
    return ScheduleListResponse(
        items=[_schedule_response(record, heartbeat, settings) for record in records]
    )


@router.get("/status", response_model=SchedulerStatusResponse)
def get_scheduler_status(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> SchedulerStatusResponse:
    """Return the dedicated scheduler heartbeat independently of configured schedules."""
    heartbeat = session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME)
    heartbeat_at = _as_utc(heartbeat.last_heartbeat_at) if heartbeat is not None else None
    online = (
        heartbeat_at is not None
        and (datetime.now(UTC) - heartbeat_at).total_seconds()
        <= settings.scheduler_offline_after_seconds
    )
    return SchedulerStatusResponse(
        status="ONLINE" if online else "OFFLINE",
        last_heartbeat_at=heartbeat_at,
        instance_id=heartbeat.instance_id if heartbeat is not None else None,
        version=heartbeat.version if heartbeat is not None else None,
    )


@router.get("/runs", response_model=ScheduleRunListResponse)
def list_schedule_runs(
    session: Annotated[Session, Depends(get_session)],
    environment: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScheduleRunListResponse:
    """Expose newest-first scheduler run history without actor identity."""
    filters = [DailyReportScheduleRunRecord.environment == environment] if environment else []
    total = (
        session.scalar(
            select(func.count()).select_from(DailyReportScheduleRunRecord).where(*filters)
        )
        or 0
    )
    records = session.scalars(
        select(DailyReportScheduleRunRecord)
        .where(*filters)
        .order_by(
            DailyReportScheduleRunRecord.scheduled_for.desc(),
            DailyReportScheduleRunRecord.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return ScheduleRunListResponse(
        items=[_run_response(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@admin_router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    body: ScheduleWriteRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> ScheduleResponse:
    """Create one environment schedule and append an audit event."""
    if session.scalar(
        select(DailyReportScheduleRecord.id).where(
            DailyReportScheduleRecord.environment == body.environment
        )
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="schedule already exists")
    now = utc_now()
    record = DailyReportScheduleRecord(
        environment=body.environment,
        enabled=body.enabled,
        display_name=body.display_name,
        report_title=body.report_title,
        notification_connection_id=_validated_notification_connection_id(session, body, settings),
        use_ai_summary=body.use_ai_summary,
        run_at_local_time=body.run_at_local_time,
        timezone=body.timezone,
        revision=1,
        created_by_user_id=_actor_id(principal),
        updated_by_user_id=_actor_id(principal),
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.flush()
    _audit(
        session, principal, "daily_report_schedule.created", record.id, _schedule_summary(record)
    )
    session.commit()
    return _schedule_response(
        record, session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME), settings
    )


@admin_router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(
    schedule_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> ScheduleResponse:
    """Return one Admin-visible schedule for a direct settings link."""
    record = session.get(DailyReportScheduleRecord, schedule_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    return _schedule_response(
        record, session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME), settings
    )


@admin_router.put("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: UUID,
    body: ScheduleUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> ScheduleResponse:
    """Update a schedule only when the UI's revision is current."""
    record = session.get(DailyReportScheduleRecord, schedule_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    if record.revision != body.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="schedule revision conflict"
        )
    duplicate = session.scalar(
        select(DailyReportScheduleRecord.id).where(
            DailyReportScheduleRecord.environment == body.environment,
            DailyReportScheduleRecord.id != schedule_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="schedule already exists")
    before = _schedule_summary(record)
    record.environment = body.environment
    record.enabled = body.enabled
    record.display_name = body.display_name
    record.report_title = body.report_title
    record.notification_connection_id = _validated_notification_connection_id(
        session, body, settings
    )
    record.use_ai_summary = body.use_ai_summary
    record.run_at_local_time = body.run_at_local_time
    record.timezone = body.timezone
    record.revision += 1
    record.updated_by_user_id = _actor_id(principal)
    record.updated_at = utc_now()
    session.flush()
    _audit(
        session,
        principal,
        "daily_report_schedule.updated",
        record.id,
        {"before": before, "after": _schedule_summary(record)},
    )
    session.commit()
    return _schedule_response(
        record, session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME), settings
    )


@admin_router.post(
    "/{schedule_id}/runs", response_model=ScheduleRunResponse, status_code=status.HTTP_202_ACCEPTED
)
def request_manual_run(
    schedule_id: UUID,
    body: ManualRunRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> ScheduleRunResponse:
    """Queue a historical UTC report date for the scheduler process."""
    if body.report_date >= datetime.now(UTC).date():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="report_date must be a completed UTC date",
        )
    schedule = session.get(DailyReportScheduleRecord, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    now = utc_now()
    run = DailyReportScheduleRunRecord(
        schedule_id=schedule.id,
        environment=schedule.environment,
        report_date=body.report_date,
        trigger_type=DailyReportScheduleRunTrigger.MANUAL,
        status=DailyReportScheduleRunStatus.CLAIMED,
        scheduled_for=now,
        requested_by_user_id=_actor_id(principal),
        created_at=now,
    )
    session.add(run)
    session.flush()
    _audit(
        session,
        principal,
        "daily_report_schedule.manual_run_requested",
        run.id,
        {"schedule_id": str(schedule.id), "report_date": body.report_date.isoformat()},
    )
    session.commit()
    return _run_response(run)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _schedule_response(
    record: DailyReportScheduleRecord,
    heartbeat: SchedulerHeartbeatRecord | None,
    settings: Settings,
) -> ScheduleResponse:
    now = datetime.now(UTC)
    heartbeat_at = _as_utc(heartbeat.last_heartbeat_at) if heartbeat is not None else None
    online = (
        heartbeat_at is not None
        and (now - heartbeat_at).total_seconds() <= settings.scheduler_offline_after_seconds
    )
    trigger = CronTrigger(
        hour=record.run_at_local_time.hour,
        minute=record.run_at_local_time.minute,
        timezone=ZoneInfo(record.timezone),
    )
    return ScheduleResponse(
        id=record.id,
        environment=record.environment,
        enabled=record.enabled,
        display_name=record.display_name,
        report_title=record.report_title,
        notification_connection_id=record.notification_connection_id,
        use_ai_summary=record.use_ai_summary,
        run_at_local_time=record.run_at_local_time,
        timezone=record.timezone,
        revision=record.revision,
        applied_revision=record.applied_revision,
        applied_at=_as_utc(record.applied_at) if record.applied_at is not None else None,
        next_run_at=_as_utc(trigger.get_next_fire_time(None, now)) if record.enabled else None,
        scheduler_status="ONLINE" if online else "OFFLINE",
        last_heartbeat_at=heartbeat_at,
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )


def _run_response(record: DailyReportScheduleRunRecord) -> ScheduleRunResponse:
    return ScheduleRunResponse(
        id=record.id,
        schedule_id=record.schedule_id,
        environment=record.environment,
        report_date=record.report_date,
        trigger_type=record.trigger_type,
        status=record.status,
        scheduled_for=_as_utc(record.scheduled_for),
        report_id=record.report_id,
        error_category=record.error_category,
        started_at=_as_utc(record.started_at) if record.started_at is not None else None,
        finished_at=_as_utc(record.finished_at) if record.finished_at is not None else None,
        created_at=_as_utc(record.created_at),
    )


def _audit(
    session: Session,
    principal: AuthenticatedPrincipal,
    action: str,
    target_id: UUID,
    change_summary: dict[str, object],
) -> None:
    session.add(
        AdminAuditEventRecord(
            actor_user_id=_actor_id(principal),
            action=action,
            target_type="daily_report_schedule",
            target_id=target_id,
            change_summary=change_summary,
            correlation_id=correlation_id_context.get(),
            created_at=utc_now(),
        )
    )


def _schedule_summary(record: DailyReportScheduleRecord) -> dict[str, object]:
    return {
        "environment": record.environment,
        "enabled": record.enabled,
        "display_name": record.display_name,
        "report_title": record.report_title,
        "notification_connection_id": str(record.notification_connection_id)
        if record.notification_connection_id
        else None,
        "use_ai_summary": record.use_ai_summary,
        "run_at_local_time": record.run_at_local_time.isoformat(),
        "timezone": record.timezone,
        "revision": record.revision,
        "applied_revision": record.applied_revision,
    }


def _actor_id(principal: AuthenticatedPrincipal) -> UUID:
    if principal.user_id is None:
        raise RuntimeError("Admin principal has no user ID")
    return principal.user_id


def _validated_notification_connection_id(
    session: Session, body: ScheduleWriteRequest, settings: Settings
) -> UUID | None:
    """Accept only an enabled Notification connection belonging to the selected environment."""
    if body.notification_connection_id is None:
        return None
    if settings.notification_config_source != "database":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="notification connection selection requires database configuration",
        )
    connection = session.get(ManagedConnectionRecord, body.notification_connection_id)
    if (
        connection is None
        or connection.environment != body.environment
        or connection.purpose != ConnectionPurpose.NOTIFICATION
        or not connection.enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="notification connection must be enabled for the selected environment",
        )
    return connection.id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
