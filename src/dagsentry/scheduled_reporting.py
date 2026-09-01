"""Durable execution boundary for APScheduler-driven Daily Reports."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.models import (
    DailyReportScheduleRecord,
    DailyReportScheduleRunRecord,
    DailyReportScheduleRunStatus,
    DailyReportScheduleRunTrigger,
)
from dagsentry.report_app import run_daily_report


def report_date_for_scheduled_time(scheduled_for: datetime) -> date:
    """Map one aware trigger time to the last completed UTC calendar date."""
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must include a timezone")
    return scheduled_for.astimezone(UTC).date() - timedelta(days=1)


def queue_scheduled_run(
    session_factory: SessionFactory,
    *,
    schedule_id: UUID,
    environment: str,
    scheduled_for: datetime,
) -> DailyReportScheduleRunRecord:
    """Create at most one durable scheduled execution for a schedule/date."""
    report_date = report_date_for_scheduled_time(scheduled_for)
    try:
        with session_factory.begin() as session:
            existing = session.scalar(
                select(DailyReportScheduleRunRecord).where(
                    DailyReportScheduleRunRecord.schedule_id == schedule_id,
                    DailyReportScheduleRunRecord.report_date == report_date,
                    DailyReportScheduleRunRecord.trigger_type
                    == DailyReportScheduleRunTrigger.SCHEDULED,
                )
            )
            if existing is not None:
                return existing
            run = DailyReportScheduleRunRecord(
                schedule_id=schedule_id,
                environment=environment,
                report_date=report_date,
                trigger_type=DailyReportScheduleRunTrigger.SCHEDULED,
                status=DailyReportScheduleRunStatus.CLAIMED,
                scheduled_for=_as_utc(scheduled_for),
            )
            session.add(run)
            session.flush()
            return run
    except IntegrityError:
        with session_factory() as session:
            existing = session.scalar(
                select(DailyReportScheduleRunRecord).where(
                    DailyReportScheduleRunRecord.schedule_id == schedule_id,
                    DailyReportScheduleRunRecord.report_date == report_date,
                    DailyReportScheduleRunRecord.trigger_type
                    == DailyReportScheduleRunTrigger.SCHEDULED,
                )
            )
            if existing is None:
                raise
            return existing


def queue_manual_run(
    session_factory: SessionFactory,
    *,
    environment: str,
    report_date: date,
    requested_by_user_id: UUID,
    requested_at: datetime,
    schedule_id: UUID | None = None,
) -> DailyReportScheduleRunRecord:
    """Queue a user-requested run for the dedicated scheduler to execute."""
    with session_factory.begin() as session:
        run = DailyReportScheduleRunRecord(
            schedule_id=schedule_id,
            environment=environment,
            report_date=report_date,
            trigger_type=DailyReportScheduleRunTrigger.MANUAL,
            status=DailyReportScheduleRunStatus.CLAIMED,
            scheduled_for=_as_utc(requested_at),
            requested_by_user_id=requested_by_user_id,
        )
        session.add(run)
        session.flush()
        return run


def execute_claimed_run(
    session_factory: SessionFactory,
    *,
    run_id: UUID,
    settings: Settings,
    now: datetime | None = None,
) -> DailyReportScheduleRunRecord | None:
    """Atomically claim, run, and finalize one queued execution."""
    started_at = _as_utc(now or datetime.now(UTC))
    with session_factory.begin() as session:
        run = session.scalar(
            select(DailyReportScheduleRunRecord)
            .where(DailyReportScheduleRunRecord.id == run_id)
            .with_for_update()
        )
        if run is None or run.status != DailyReportScheduleRunStatus.CLAIMED:
            return None
        run.status = DailyReportScheduleRunStatus.RUNNING
        run.started_at = started_at

    with session_factory() as session:
        run = session.get(DailyReportScheduleRunRecord, run_id)
        if run is None:
            return None
        schedule = (
            session.get(DailyReportScheduleRecord, run.schedule_id) if run.schedule_id else None
        )
        environment = run.environment
        report_date = run.report_date

    try:
        result = run_daily_report(
            report_date,
            settings,
            environment=environment,
            notification_connection_id=(
                schedule.notification_connection_id if schedule is not None else None
            ),
            use_ai_summary=schedule.use_ai_summary if schedule is not None else True,
            report_title=schedule.report_title if schedule is not None else None,
        )
    except Exception as error:
        _finish_run(
            session_factory,
            run_id=run_id,
            status=DailyReportScheduleRunStatus.FAILED,
            finished_at=_as_utc(datetime.now(UTC)),
            error_category=_error_category(error),
        )
        raise

    return _finish_run(
        session_factory,
        run_id=run_id,
        status=(
            DailyReportScheduleRunStatus.SUCCEEDED
            if result.delivered or result.delivery_status.value == "DELIVERED"
            else DailyReportScheduleRunStatus.SKIPPED
        ),
        finished_at=_as_utc(datetime.now(UTC)),
        report_id=result.report_id,
    )


def execute_pending_manual_runs(
    session_factory: SessionFactory,
    *,
    settings: Settings,
    limit: int = 20,
) -> list[UUID]:
    """Run queued manual requests; concurrent schedulers race safely per run ID."""
    with session_factory() as session:
        run_ids = session.scalars(
            select(DailyReportScheduleRunRecord.id)
            .where(
                DailyReportScheduleRunRecord.trigger_type == DailyReportScheduleRunTrigger.MANUAL,
                DailyReportScheduleRunRecord.status == DailyReportScheduleRunStatus.CLAIMED,
            )
            .order_by(DailyReportScheduleRunRecord.scheduled_for, DailyReportScheduleRunRecord.id)
            .limit(limit)
        ).all()
    completed: list[UUID] = []
    for run_id in run_ids:
        if execute_claimed_run(session_factory, run_id=run_id, settings=settings) is not None:
            completed.append(run_id)
    return completed


def _finish_run(
    session_factory: SessionFactory,
    *,
    run_id: UUID,
    status: DailyReportScheduleRunStatus,
    finished_at: datetime,
    report_id: UUID | None = None,
    error_category: str | None = None,
) -> DailyReportScheduleRunRecord:
    with session_factory.begin() as session:
        run = session.get(DailyReportScheduleRunRecord, run_id)
        if run is None:
            raise RuntimeError("daily report schedule run disappeared")
        run.status = status
        run.report_id = report_id
        run.error_category = error_category
        run.finished_at = finished_at
        session.flush()
        return run


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def _error_category(error: Exception) -> str:
    return error.__class__.__name__[:50]
