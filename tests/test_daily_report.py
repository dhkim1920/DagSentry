from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from dagsentry.daily_report import DailyReportService, build_rule_based_report
from dagsentry.db import SessionFactory
from dagsentry.domain.diagnosis import ErrorClassification
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationErrorCategory,
    NotificationProviderError,
)
from dagsentry.domain.reporting import (
    DailyReportAISummary,
    DailyReportNotificationPayload,
    DailyReportSummaryProviderError,
    DailyStatistics,
    ErrorSignatureStatistics,
    IncidentStatistics,
    MeanTimeStatistics,
)
from dagsentry.models import DailyReportRecord

REPORT_DATE = date(2026, 8, 12)


def statistics() -> DailyStatistics:
    start = datetime.combine(REPORT_DATE, time.min, tzinfo=UTC)
    counts = dict.fromkeys(ErrorClassification, 0)
    counts[ErrorClassification.NETWORK] = 3
    return DailyStatistics(
        report_date=REPORT_DATE,
        period_start=start,
        period_end=start + timedelta(days=1),
        environment="production",
        failure_attempts=3,
        affected_task_instances=2,
        affected_dag_runs=1,
        incidents=IncidentStatistics(new=1, unresolved=1, recovered=0),
        error_signatures=ErrorSignatureStatistics(new=1, repeated=2),
        classification_counts=counts,
        mean_time=MeanTimeStatistics(recovery_seconds=None, resolution_seconds=120.0),
    )


class StubNotificationProvider:
    name = "stub"

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[DailyReportNotificationPayload, str]] = []

    def send(self, payload: DailyReportNotificationPayload, *, delivery_key: str) -> int:
        self.calls.append((payload, delivery_key))
        if len(self.calls) <= self.failures:
            raise NotificationProviderError(
                "temporarily unavailable",
                category=NotificationErrorCategory.UNAVAILABLE,
                retryable=True,
                response_status=503,
            )
        return 204


class StubSummaryProvider:
    name = "stub-ai"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def summarize(
        self, statistics: DailyStatistics, rule_based_report: object
    ) -> DailyReportAISummary:
        self.calls += 1
        if self.fail:
            raise DailyReportSummaryProviderError("invalid response")
        assert statistics.failure_attempts >= 0
        assert rule_based_report is not None
        return DailyReportAISummary(
            key_changes=("Repeated network failures remain the primary signal.",),
            priorities=("Inspect the unresolved network incident first.",),
        )


def test_rule_based_report_is_deterministic_and_complete() -> None:
    first = build_rule_based_report(statistics())
    second = build_rule_based_report(statistics())

    assert first == second
    assert "실패 3회" in first.overview
    assert "NETWORK=3" in first.highlights[2]
    assert first.priorities


def test_rule_based_report_uses_configured_report_title() -> None:
    report = build_rule_based_report(statistics(), report_title="운영 일일 리포트")

    assert report.title == "운영 일일 리포트 — production — 2026-08-12"


def test_ai_summary_schema_cannot_contain_replacement_statistics() -> None:
    with pytest.raises(ValidationError):
        DailyReportAISummary.model_validate(
            {
                "key_changes": ["A change"],
                "priorities": ["A priority"],
                "statistics": {"failure_attempts": 999},
            }
        )

    with pytest.raises(ValidationError):
        statistics().failure_attempts = 999


def test_report_without_ai_is_created_and_delivered_once(
    session_factory: SessionFactory,
) -> None:
    notification = StubNotificationProvider()
    service = DailyReportService(session_factory, notification_provider=notification)

    first = service.run(REPORT_DATE, "production")
    second = service.run(REPORT_DATE, "production")

    assert first.created is True
    assert first.ai_summary_used is False
    assert first.delivered is True
    assert second.created is False
    assert second.delivered is False
    assert len(notification.calls) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyReportRecord)) == 1
        record = session.scalar(select(DailyReportRecord))
        assert record is not None
        assert record.statistics["failure_attempts"] == 0
        assert record.status == NotificationDeliveryStatus.DELIVERED


def test_ai_failure_falls_back_to_rule_report(
    session_factory: SessionFactory,
) -> None:
    notification = StubNotificationProvider()
    summary = StubSummaryProvider(fail=True)

    result = DailyReportService(
        session_factory,
        notification_provider=notification,
        summary_provider=summary,
    ).run(REPORT_DATE, "production")

    assert result.ai_summary_used is False
    assert summary.calls == 1
    assert notification.calls[0][0].ai_summary is None
    assert notification.calls[0][0].rule_based_report.priorities


def test_failed_delivery_retries_stored_report_without_regeneration(
    session_factory: SessionFactory,
) -> None:
    notification = StubNotificationProvider(failures=1)
    summary = StubSummaryProvider()
    service = DailyReportService(
        session_factory,
        notification_provider=notification,
        summary_provider=summary,
    )

    with pytest.raises(NotificationProviderError):
        service.run(REPORT_DATE, "production")
    result = service.run(REPORT_DATE, "production")

    assert result.created is False
    assert result.ai_summary_used is True
    assert result.delivery_status == NotificationDeliveryStatus.DELIVERED
    assert summary.calls == 1
    assert len(notification.calls) == 2
    assert notification.calls[0] == notification.calls[1]
    with session_factory() as session:
        record = session.scalar(select(DailyReportRecord))
        assert record is not None
        assert record.attempt_count == 2
