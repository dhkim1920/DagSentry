from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from dagsentry.config import Settings
from dagsentry.daily_report import DailyReportRunResult
from dagsentry.db import SessionFactory
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.models import (
    DailyReportScheduleRecord,
    DailyReportScheduleRunRecord,
    DailyReportScheduleRunStatus,
    SchedulerHeartbeatRecord,
)
from dagsentry.scheduled_reporting import (
    execute_claimed_run,
    queue_manual_run,
    queue_scheduled_run,
    report_date_for_scheduled_time,
)
from dagsentry.scheduler import DailyReportScheduler, _last_scheduled_fire_time
from dagsentry.scheduler_health import check_scheduler_health


def add_schedule(
    session_factory: SessionFactory, *, enabled: bool = True
) -> DailyReportScheduleRecord:
    with session_factory.begin() as session:
        record = DailyReportScheduleRecord(
            environment="production",
            enabled=enabled,
            run_at_local_time=time(9, 10),
            timezone="Asia/Seoul",
            revision=1,
        )
        session.add(record)
        session.flush()
        return record


def test_scheduled_time_uses_previous_completed_utc_date() -> None:
    assert report_date_for_scheduled_time(datetime(2026, 8, 30, 9, 10, tzinfo=UTC)) == date(
        2026, 8, 29
    )
    with pytest.raises(ValueError, match="timezone"):
        report_date_for_scheduled_time(datetime(2026, 8, 30, 9, 10))


def test_delayed_job_uses_its_local_scheduled_fire_time() -> None:
    scheduled = _last_scheduled_fire_time(
        datetime(2026, 8, 30, 0, 20, tzinfo=UTC),
        run_at_local_time=time(9, 10),
        timezone=ZoneInfo("Asia/Seoul"),
    )

    assert scheduled == datetime(2026, 8, 30, 0, 10, tzinfo=UTC)


def test_scheduled_claim_is_unique_and_manual_requests_remain_distinct(
    session_factory: SessionFactory,
) -> None:
    schedule = add_schedule(session_factory)
    scheduled_for = datetime(2026, 8, 30, 0, 10, tzinfo=UTC)

    first = queue_scheduled_run(
        session_factory,
        schedule_id=schedule.id,
        environment="production",
        scheduled_for=scheduled_for,
    )
    second = queue_scheduled_run(
        session_factory,
        schedule_id=schedule.id,
        environment="production",
        scheduled_for=scheduled_for,
    )
    manual = queue_manual_run(
        session_factory,
        schedule_id=schedule.id,
        environment="production",
        report_date=date(2026, 8, 29),
        requested_by_user_id=uuid4(),
        requested_at=scheduled_for,
    )

    assert first.id == second.id
    assert manual.id != first.id
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyReportScheduleRunRecord)) == 2


def test_claimed_run_executes_daily_report_once_and_records_result(
    settings: Settings,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = add_schedule(session_factory)
    queued = queue_scheduled_run(
        session_factory,
        schedule_id=schedule.id,
        environment="production",
        scheduled_for=datetime(2026, 8, 30, 0, 10, tzinfo=UTC),
    )
    report_id = queued.id
    calls: list[tuple[date, str]] = []
    expected_factory = session_factory

    def run_report(
        report_date: date,
        _: Settings,
        *,
        environment: str,
        notification_connection_id: object,
        use_ai_summary: bool,
        report_title: str | None,
        timezone: str,
        session_factory: SessionFactory,
    ) -> DailyReportRunResult:
        assert timezone == schedule.timezone
        assert session_factory is expected_factory
        calls.append((report_date, environment))
        return DailyReportRunResult(
            report_id=report_id,
            created=True,
            ai_summary_used=False,
            delivered=True,
            delivery_status=NotificationDeliveryStatus.DELIVERED,
        )

    monkeypatch.setattr("dagsentry.scheduled_reporting.run_daily_report", run_report)
    completed = execute_claimed_run(session_factory, run_id=queued.id, settings=settings)
    duplicate = execute_claimed_run(session_factory, run_id=queued.id, settings=settings)

    assert calls == [(date(2026, 8, 29), "production")]
    assert completed is not None
    assert completed.status == DailyReportScheduleRunStatus.SUCCEEDED
    assert completed.report_id == report_id
    assert duplicate is None


def test_scheduler_reconciles_db_schedule_and_writes_heartbeat(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    schedule = add_schedule(session_factory)
    scheduler = DailyReportScheduler(settings, session_factory)

    scheduler.reconcile()
    scheduler.heartbeat()

    job = scheduler.scheduler.get_job(f"daily-report:{schedule.id}")
    assert job is not None
    assert job.max_instances == 1
    with session_factory() as session:
        heartbeat = session.get(SchedulerHeartbeatRecord, "daily-report")
        applied = session.get(DailyReportScheduleRecord, schedule.id)
        assert heartbeat is not None
        assert applied is not None and applied.applied_revision == applied.revision

    with session_factory.begin() as session:
        record = session.get(DailyReportScheduleRecord, schedule.id)
        assert record is not None
        record.enabled = False
        record.revision += 1
    scheduler.reconcile()

    assert scheduler.scheduler.get_job(f"daily-report:{schedule.id}") is None
    with session_factory() as session:
        applied = session.get(DailyReportScheduleRecord, schedule.id)
        assert applied is not None and applied.applied_revision == applied.revision
    scheduler.shutdown()


def test_scheduler_health_rejects_missing_and_stale_heartbeats(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    now = datetime(2026, 8, 30, 0, 10, tzinfo=UTC)
    missing, _ = check_scheduler_health(settings, session_factory, now=now)
    assert missing is False
    with session_factory.begin() as session:
        session.add(
            SchedulerHeartbeatRecord(
                scheduler_name="daily-report",
                instance_id=uuid4(),
                started_at=now - timedelta(seconds=31),
                last_heartbeat_at=now - timedelta(seconds=31),
                version="test",
            )
        )
    stale, _ = check_scheduler_health(settings, session_factory, now=now)
    fresh, _ = check_scheduler_health(
        settings,
        session_factory,
        now=now,
        max_age_seconds=31,
    )

    assert stale is False
    assert fresh is True
