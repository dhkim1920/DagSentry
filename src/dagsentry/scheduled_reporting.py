"""Durable execution boundary for APScheduler-driven Daily Reports."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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

logger = logging.getLogger(__name__)


def report_date_for_scheduled_time(scheduled_for: datetime, timezone: str = "Asia/Seoul") -> date:
    """Map one aware trigger time to the last completed UTC calendar date."""
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must include a timezone")
    return scheduled_for.astimezone(ZoneInfo(timezone)).date() - timedelta(days=1)


def queue_scheduled_run(
    session_factory: SessionFactory,
    *,
    schedule_id: UUID,
    environment: str,
    scheduled_for: datetime,
) -> DailyReportScheduleRunRecord:
    """Create at most one durable scheduled execution for a schedule/date."""
    with session_factory() as session:
        schedule = session.get(DailyReportScheduleRecord, schedule_id)
        if schedule is None:
            raise ValueError("Daily Report schedule was not found")
        report_date = report_date_for_scheduled_time(scheduled_for, schedule.timezone)
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
            .with_for_update(skip_locked=True)
        )
        if run is None or run.status != DailyReportScheduleRunStatus.CLAIMED:
            return None
        run.status = DailyReportScheduleRunStatus.RUNNING
        run.started_at = started_at

    report_error: Exception | None = None
    # Keep this row locked during execution. Recovery skips live executions even
    # when they exceed the stale timeout; a dead process releases its DB locks.
    with session_factory.begin() as session:
        run = session.scalar(
            select(DailyReportScheduleRunRecord)
            .where(
                DailyReportScheduleRunRecord.id == run_id,
                DailyReportScheduleRunRecord.status == DailyReportScheduleRunStatus.RUNNING,
                DailyReportScheduleRunRecord.started_at == started_at,
            )
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        schedule = (
            session.get(DailyReportScheduleRecord, run.schedule_id) if run.schedule_id else None
        )
        try:
            result = run_daily_report(
                run.report_date,
                settings,
                environment=run.environment,
                notification_connection_id=(
                    schedule.notification_connection_id if schedule is not None else None
                ),
                use_ai_summary=schedule.use_ai_summary if schedule is not None else True,
                report_title=schedule.report_title if schedule is not None else None,
                timezone=schedule.timezone if schedule is not None else settings.report_timezone,
                session_factory=session_factory,
            )
        except Exception as error:
            run.status = DailyReportScheduleRunStatus.FAILED
            run.error_category = _error_category(error)
            report_error = error
        else:
            run.status = (
                DailyReportScheduleRunStatus.SUCCEEDED
                if result.delivered or result.delivery_status.value == "DELIVERED"
                else DailyReportScheduleRunStatus.SKIPPED
            )
            run.report_id = result.report_id
            run.error_category = None
        run.finished_at = datetime.now(UTC)
    # Commit the failed state before propagating the Provider error.
    if report_error is not None:
        raise report_error
    return run


def recover_stale_runs(
    session_factory: SessionFactory,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
    limit: int = 20,
) -> list[UUID]:
    """Requeue interrupted runs, skipping row locks held by live executions."""
    stale_before = _as_utc(now or datetime.now(UTC)) - timedelta(seconds=stale_after_seconds)
    with session_factory.begin() as session:
        runs = session.scalars(
            select(DailyReportScheduleRunRecord)
            .where(
                DailyReportScheduleRunRecord.status == DailyReportScheduleRunStatus.RUNNING,
                DailyReportScheduleRunRecord.started_at <= stale_before,
            )
            .order_by(DailyReportScheduleRunRecord.started_at, DailyReportScheduleRunRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        recovered = [run.id for run in runs]
        for run in runs:
            run.status = DailyReportScheduleRunStatus.CLAIMED
            run.started_at = None
            run.finished_at = None
            run.error_category = None
    for run_id in recovered:
        logger.warning("interrupted daily report reclaimed run_id=%s", run_id)
    return recovered


def execute_pending_runs(
    session_factory: SessionFactory,
    *,
    settings: Settings,
    limit: int = 20,
) -> list[UUID]:
    """Consume queued or recovered runs independently, including scheduled claims."""
    with session_factory() as session:
        run_ids = session.scalars(
            select(DailyReportScheduleRunRecord.id)
            .where(
                DailyReportScheduleRunRecord.status == DailyReportScheduleRunStatus.CLAIMED,
            )
            .order_by(DailyReportScheduleRunRecord.scheduled_for, DailyReportScheduleRunRecord.id)
            .limit(limit)
        ).all()
    completed: list[UUID] = []
    for run_id in run_ids:
        try:
            result = execute_claimed_run(session_factory, run_id=run_id, settings=settings)
        except Exception:
            logger.exception("queued daily report failed run_id=%s", run_id)
            continue
        if result is not None:
            completed.append(run_id)
    return completed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def _error_category(error: Exception) -> str:
    return error.__class__.__name__[:50]
