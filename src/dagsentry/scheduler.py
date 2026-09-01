"""DB-reconciled APScheduler process for Daily Report automation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError  # type: ignore[import-untyped]
from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy import select

from dagsentry import __version__
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.models import (
    DailyReportScheduleRecord,
    SchedulerHeartbeatRecord,
)
from dagsentry.scheduled_reporting import (
    execute_claimed_run,
    execute_pending_manual_runs,
    queue_scheduled_run,
)

logger = logging.getLogger(__name__)
SCHEDULER_NAME = "daily-report"


class DailyReportScheduler:
    """Keep APScheduler's memory jobs synchronized with DB schedule records."""

    def __init__(self, settings: Settings, session_factory: SessionFactory) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.instance_id = uuid4()
        self.started_at = datetime.now(UTC)
        self.scheduler = BlockingScheduler(timezone=UTC)

    def run_forever(self) -> None:
        """Start the dedicated process and block until a shutdown signal arrives."""
        self.reconcile()
        self.heartbeat()
        self.scheduler.add_job(
            self.reconcile,
            "interval",
            seconds=self.settings.scheduler_poll_interval_seconds,
            id="daily-report:reconcile",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.heartbeat,
            "interval",
            seconds=self.settings.scheduler_heartbeat_interval_seconds,
            id="daily-report:heartbeat",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

    def shutdown(self) -> None:
        """Stop accepting new jobs and wait for executing Daily Reports to finish."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    def heartbeat(self) -> None:
        """Persist the scheduler's current liveness signal."""
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            heartbeat = session.get(SchedulerHeartbeatRecord, SCHEDULER_NAME)
            if heartbeat is None:
                heartbeat = SchedulerHeartbeatRecord(
                    scheduler_name=SCHEDULER_NAME,
                    instance_id=self.instance_id,
                    started_at=self.started_at,
                    last_heartbeat_at=now,
                    version=__version__,
                )
                session.add(heartbeat)
            else:
                heartbeat.instance_id = self.instance_id
                heartbeat.started_at = self.started_at
                heartbeat.last_heartbeat_at = now
                heartbeat.version = __version__

    def reconcile(self) -> None:
        """Apply all enabled DB schedule revisions and consume manual requests."""
        with self.session_factory() as session:
            schedules = session.scalars(select(DailyReportScheduleRecord)).all()
        active_ids: set[str] = set()
        for schedule in schedules:
            job_id = _job_id(schedule.id)
            if not schedule.enabled:
                self._remove_job(job_id)
                if schedule.applied_revision != schedule.revision:
                    self._mark_schedule_applied(schedule.id, schedule.revision)
                continue
            active_ids.add(job_id)
            job = self.scheduler.get_job(job_id)
            if job is not None and schedule.applied_revision == schedule.revision:
                continue
            timezone = ZoneInfo(schedule.timezone)
            trigger = CronTrigger(
                hour=schedule.run_at_local_time.hour,
                minute=schedule.run_at_local_time.minute,
                timezone=timezone,
            )
            self.scheduler.add_job(
                self._run_scheduled,
                trigger=trigger,
                args=[schedule.id],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            self._mark_schedule_applied(schedule.id, schedule.revision)
        for job in self.scheduler.get_jobs():
            if (
                job.id.startswith("daily-report:")
                and job.id
                not in {
                    "daily-report:reconcile",
                    "daily-report:heartbeat",
                }
                and job.id not in active_ids
            ):
                self._remove_job(job.id)
        execute_pending_manual_runs(self.session_factory, settings=self.settings)

    def _run_scheduled(self, schedule_id: UUID) -> None:
        """Queue then execute one schedule occurrence through the durable run boundary."""
        now = datetime.now(UTC)
        with self.session_factory() as session:
            schedule = session.get(DailyReportScheduleRecord, schedule_id)
            if schedule is None or not schedule.enabled:
                return
            environment = schedule.environment
        scheduled_for = _last_scheduled_fire_time(
            now,
            run_at_local_time=schedule.run_at_local_time,
            timezone=ZoneInfo(schedule.timezone),
        )
        run = queue_scheduled_run(
            self.session_factory,
            schedule_id=schedule_id,
            environment=environment,
            scheduled_for=scheduled_for,
        )
        try:
            execute_claimed_run(self.session_factory, run_id=run.id, settings=self.settings)
        except Exception:
            logger.exception("scheduled daily report failed run_id=%s", run.id)

    def _remove_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            pass

    def _mark_schedule_applied(self, schedule_id: UUID, revision: int) -> None:
        with self.session_factory.begin() as session:
            schedule = session.get(DailyReportScheduleRecord, schedule_id)
            if schedule is not None and schedule.revision == revision:
                schedule.applied_revision = revision
                schedule.applied_at = datetime.now(UTC)


def _job_id(schedule_id: UUID) -> str:
    return f"daily-report:{schedule_id}"


def _last_scheduled_fire_time(
    now: datetime,
    *,
    run_at_local_time: time,
    timezone: ZoneInfo,
) -> datetime:
    """Recover the current job's intended local fire time after a short delay."""
    local_now = now.astimezone(timezone)
    scheduled = datetime.combine(local_now.date(), run_at_local_time, tzinfo=timezone)
    if scheduled > local_now:
        scheduled -= timedelta(days=1)
    return scheduled.astimezone(UTC)
