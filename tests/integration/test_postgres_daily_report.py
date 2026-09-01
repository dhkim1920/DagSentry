from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from dagsentry.daily_report import DailyReportService
from dagsentry.db import create_session_factory
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.models import DailyReportRecord

pytestmark = pytest.mark.integration


class CapturingProvider:
    name = "integration-stub"

    def __init__(self) -> None:
        self.calls: list[tuple[DailyReportNotificationPayload, str]] = []

    def send(self, payload: DailyReportNotificationPayload, *, delivery_key: str) -> int:
        self.calls.append((payload, delivery_key))
        return 204


def test_postgres_report_is_persisted_and_delivered_once() -> None:
    database_url = os.environ.get("DAGSENTRY_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("DAGSENTRY_TEST_DATABASE_URL is not configured")

    session_factory = create_session_factory(database_url)
    environment = f"daily-{uuid4().hex[:12]}"
    report_date = date(2026, 8, 12)
    provider = CapturingProvider()
    service = DailyReportService(session_factory, notification_provider=provider)

    try:
        first = service.run(report_date, environment)
        second = service.run(report_date, environment)

        assert first.created is True
        assert first.delivered is True
        assert second.created is False
        assert second.delivered is False
        assert len(provider.calls) == 1
        with session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(DailyReportRecord)
                .where(
                    DailyReportRecord.report_date == report_date,
                    DailyReportRecord.environment == environment,
                )
            )
            assert count == 1
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(DailyReportRecord).where(DailyReportRecord.environment == environment)
            )
