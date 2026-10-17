from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from dagsentry import scheduled_reporting
from dagsentry.config import Settings
from dagsentry.daily_report import DailyReportRunResult, DailyReportService
from dagsentry.db import SessionFactory, create_session_factory
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.models import (
    DailyReportRecord,
    DailyReportScheduleRecord,
    DailyReportScheduleRunRecord,
    DailyReportScheduleRunStatus,
    DailyReportScheduleRunTrigger,
)
from dagsentry.scheduler import DailyReportScheduler

pytestmark = pytest.mark.integration


class CapturingProvider:
    name = "scheduler-integration"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, payload: DailyReportNotificationPayload, *, delivery_key: str) -> int:
        self.calls += 1
        return 204


@pytest.fixture
def recovery_database() -> Iterator[tuple[SessionFactory, DailyReportScheduleRecord]]:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")
    factory = create_session_factory(database_url)
    environment = f"scheduler-{uuid4().hex[:12]}"
    with factory.begin() as session:
        schedule = DailyReportScheduleRecord(
            environment=environment,
            enabled=True,
            run_at_local_time=time(9),
            timezone="Asia/Seoul",
        )
        session.add(schedule)
        session.flush()
    try:
        yield factory, schedule
    finally:
        with factory.begin() as session:
            session.execute(
                delete(DailyReportScheduleRunRecord).where(
                    DailyReportScheduleRunRecord.environment == environment
                )
            )
            session.execute(
                delete(DailyReportScheduleRecord).where(
                    DailyReportScheduleRecord.environment == environment
                )
            )
            session.execute(
                delete(DailyReportRecord).where(DailyReportRecord.environment == environment)
            )


def add_run(
    factory: SessionFactory,
    schedule: DailyReportScheduleRecord,
    *,
    status: DailyReportScheduleRunStatus = DailyReportScheduleRunStatus.CLAIMED,
    trigger: DailyReportScheduleRunTrigger = DailyReportScheduleRunTrigger.SCHEDULED,
) -> DailyReportScheduleRunRecord:
    with factory.begin() as session:
        run = DailyReportScheduleRunRecord(
            schedule_id=schedule.id,
            environment=schedule.environment,
            report_date=date(2026, 8, 29),
            trigger_type=trigger,
            status=status,
            scheduled_for=datetime(2026, 8, 30, 0, 10, tzinfo=UTC),
            started_at=(
                datetime.now(UTC) - timedelta(seconds=901)
                if status == DailyReportScheduleRunStatus.RUNNING
                else None
            ),
        )
        session.add(run)
        session.flush()
        return run


def test_recovery_skips_a_live_execution_even_after_stale_timeout(
    recovery_database: tuple[SessionFactory, DailyReportScheduleRecord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, schedule = recovery_database
    run = add_run(factory, schedule)
    settings = Settings()
    entered = Event()
    release = Event()
    provider = CapturingProvider()

    def run_report(report_date: date, *_: object, **__: object) -> DailyReportRunResult:
        entered.set()
        assert release.wait(10)
        return DailyReportService(factory, notification_provider=provider).run(
            report_date, schedule.environment
        )

    monkeypatch.setattr(scheduled_reporting, "run_daily_report", run_report)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            scheduled_reporting.execute_claimed_run,
            factory,
            run_id=run.id,
            settings=settings,
            now=datetime.now(UTC) - timedelta(seconds=901),
        )
        try:
            assert entered.wait(10)
            with factory() as session:
                active = session.get(DailyReportScheduleRunRecord, run.id)
                assert active is not None
                assert active.status == DailyReportScheduleRunStatus.RUNNING
            assert scheduled_reporting.recover_stale_runs(factory, stale_after_seconds=900) == []
            assert (
                scheduled_reporting.execute_claimed_run(factory, run_id=run.id, settings=settings)
                is None
            )
        finally:
            release.set()
        completed = future.result(timeout=10)

    assert completed is not None and completed.status == DailyReportScheduleRunStatus.SUCCEEDED
    assert provider.calls == 1


def test_two_schedulers_recover_an_interrupted_run_once(
    recovery_database: tuple[SessionFactory, DailyReportScheduleRecord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, schedule = recovery_database
    run = add_run(factory, schedule, status=DailyReportScheduleRunStatus.RUNNING)
    provider = CapturingProvider()
    barrier = Barrier(2)
    recover = scheduled_reporting.recover_stale_runs

    def simultaneous_recovery(
        session_factory: SessionFactory, *, stale_after_seconds: float
    ) -> list[UUID]:
        barrier.wait(timeout=10)
        return recover(session_factory, stale_after_seconds=stale_after_seconds)

    def run_report(report_date: date, *_: object, **__: object) -> DailyReportRunResult:
        return DailyReportService(factory, notification_provider=provider).run(
            report_date, schedule.environment
        )

    monkeypatch.setattr("dagsentry.scheduler.recover_stale_runs", simultaneous_recovery)
    monkeypatch.setattr(scheduled_reporting, "run_daily_report", run_report)
    settings = Settings()
    schedulers = [DailyReportScheduler(settings, factory) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(scheduler.reconcile) for scheduler in schedulers]
        for future in futures:
            future.result(timeout=10)

    assert provider.calls == 1
    with factory() as session:
        recovered = session.get(DailyReportScheduleRunRecord, run.id)
        assert recovered is not None
        assert recovered.status == DailyReportScheduleRunStatus.SUCCEEDED


@pytest.mark.parametrize("delivered_before_crash", [False, True])
@pytest.mark.parametrize("trigger", list(DailyReportScheduleRunTrigger))
def test_process_crash_releases_run_lock_and_recovery_reuses_delivery(
    recovery_database: tuple[SessionFactory, DailyReportScheduleRecord],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    delivered_before_crash: bool,
    trigger: DailyReportScheduleRunTrigger,
) -> None:
    factory, schedule = recovery_database
    run = add_run(factory, schedule, trigger=trigger)
    delivery_file = tmp_path / "deliveries"
    child_script = """
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from dagsentry import scheduled_reporting
from dagsentry.config import Settings
from dagsentry.daily_report import DailyReportService
from dagsentry.db import create_session_factory

factory = create_session_factory(os.environ['DAGSENTRY_TEST_DATABASE_URL'])
class Provider:
    name = 'scheduler-integration'
    def send(self, payload, *, delivery_key):
        Path(sys.argv[3]).write_text(delivery_key)
        return 204

def crash(report_date, settings, *, environment, **kwargs):
    if sys.argv[2] == 'True':
        DailyReportService(factory, notification_provider=Provider()).run(report_date, environment)
    os._exit(17)

scheduled_reporting.run_daily_report = crash
scheduled_reporting.execute_claimed_run(
    factory, run_id=UUID(sys.argv[1]), settings=Settings(),
    now=datetime.now(UTC) - timedelta(seconds=901),
)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            child_script,
            str(run.id),
            str(delivered_before_crash),
            str(delivery_file),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 17, child.stderr
    assert delivery_file.exists() is delivered_before_crash
    with factory() as session:
        interrupted = session.get(DailyReportScheduleRunRecord, run.id)
        assert interrupted is not None
        assert interrupted.status == DailyReportScheduleRunStatus.RUNNING

    provider = CapturingProvider()

    def run_report(report_date: date, *_: object, **__: object) -> DailyReportRunResult:
        return DailyReportService(factory, notification_provider=provider).run(
            report_date, schedule.environment
        )

    monkeypatch.setattr(scheduled_reporting, "run_daily_report", run_report)
    scheduler = DailyReportScheduler(Settings(), factory)
    scheduler.reconcile()
    scheduler.reconcile()

    assert provider.calls == (0 if delivered_before_crash else 1)
    with factory() as session:
        recovered = session.get(DailyReportScheduleRunRecord, run.id)
        report = session.scalar(
            select(DailyReportRecord).where(DailyReportRecord.environment == schedule.environment)
        )
        assert recovered is not None and report is not None
        assert recovered.status == DailyReportScheduleRunStatus.SUCCEEDED
        assert recovered.report_id == report.id
        assert report.status == NotificationDeliveryStatus.DELIVERED
        assert report.attempt_count == 1
