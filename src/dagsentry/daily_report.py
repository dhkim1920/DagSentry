"""Deterministic daily report generation and idempotent delivery."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dagsentry.db import SessionFactory
from dagsentry.domain.diagnosis import ErrorClassification
from dagsentry.domain.notification import (
    NotificationDeliveryStatus,
    NotificationProviderError,
)
from dagsentry.domain.reporting import (
    REPORT_SCHEMA_VERSION,
    DailyReportAISummary,
    DailyReportNotificationPayload,
    DailyReportNotificationProvider,
    DailyReportSummaryProvider,
    DailyReportSummaryProviderError,
    DailyStatistics,
    RuleBasedDailyReport,
    validate_timezone,
)
from dagsentry.models import DailyReportRecord
from dagsentry.providers.notification_adapter import provider_names_overlap
from dagsentry.reporting import aggregate_daily_statistics

logger = logging.getLogger(__name__)
REPORT_DELIVERY_KEY_VERSION = 1


@dataclass(frozen=True)
class DailyReportRunResult:
    """Observable outcome of one idempotent report execution."""

    report_id: UUID
    created: bool
    ai_summary_used: bool
    delivered: bool
    delivery_status: NotificationDeliveryStatus


def build_rule_based_report(
    statistics: DailyStatistics, *, report_title: str | None = None
) -> RuleBasedDailyReport:
    """Render a complete report using only versioned calculated Statistics."""
    incident = statistics.incidents
    signatures = statistics.error_signatures
    classifications = [
        f"{classification.value}={statistics.classification_counts[classification]}"
        for classification in ErrorClassification
        if statistics.classification_counts[classification] > 0
    ]
    classification_summary = ", ".join(classifications) if classifications else "없음"

    priorities: list[str] = []
    if incident.unresolved:
        priorities.append(f"미해결 장애 {incident.unresolved}건을 우선 점검하세요.")
    if signatures.repeated:
        priorities.append(f"반복 오류 서명 {signatures.repeated}건의 재발 원인을 확인하세요.")
    if statistics.failure_attempts:
        priorities.append("실패가 많은 Task와 영향을 받은 실행을 점검하세요.")
    if not priorities:
        priorities.append("해당 기간에 실패로 인한 조치가 필요하지 않습니다.")

    return RuleBasedDailyReport(
        title=(
            f"{report_title or 'DagSentry 일일 장애 리포트'} — "
            f"{statistics.environment} — {statistics.report_date}"
        ),
        overview=(
            f"{statistics.timezone} 기준 실패 {statistics.failure_attempts}회, "
            f"영향받은 TaskInstance {statistics.affected_task_instances}개, "
            f"DAG 실행 {statistics.affected_dag_runs}개입니다."
        ),
        highlights=(
            f"장애: 신규 {incident.new}건, 미해결 {incident.unresolved}건, "
            f"복구 {incident.recovered}건.",
            f"오류 서명: 신규 {signatures.new}건, 반복 {signatures.repeated}건.",
            f"분류: {classification_summary}.",
            "평균 소요 시간: 복구="
            f"{_format_duration(statistics.mean_time.recovery_seconds)}, 해결="
            f"{_format_duration(statistics.mean_time.resolution_seconds)}.",
        ),
        priorities=tuple(priorities),
    )


class DailyReportService:
    """Persist one report per UTC date/environment and deliver its snapshot once."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        notification_provider: DailyReportNotificationProvider,
        summary_provider: DailyReportSummaryProvider | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timezone: str = "Asia/Seoul",
    ) -> None:
        self.session_factory = session_factory
        self.notification_provider = notification_provider
        self.summary_provider = summary_provider
        self.clock = clock
        self.timezone = validate_timezone(timezone)

    def run(
        self, report_date: date, environment: str, *, report_title: str | None = None
    ) -> DailyReportRunResult:
        """Generate if absent, then deliver the immutable stored report snapshot."""
        report_id, created = self._get_or_create(
            report_date, environment, report_title=report_title
        )
        delivered = self._deliver(report_id)
        with self.session_factory() as session:
            record = session.get(DailyReportRecord, report_id)
            if record is None:  # pragma: no cover - database invariant
                raise RuntimeError("Daily report disappeared")
            return DailyReportRunResult(
                report_id=record.id,
                created=created,
                ai_summary_used=record.ai_summary is not None,
                delivered=delivered,
                delivery_status=record.status,
            )

    def _get_or_create(
        self, report_date: date, environment: str, *, report_title: str | None
    ) -> tuple[UUID, bool]:
        with self.session_factory() as session:
            existing = session.scalar(
                select(DailyReportRecord).where(
                    DailyReportRecord.report_date == report_date,
                    DailyReportRecord.environment == environment,
                )
            )
            if existing is not None:
                return existing.id, False

            statistics = aggregate_daily_statistics(
                session,
                report_date=report_date,
                environment=environment,
                timezone=self.timezone,
            )
            rule_report = build_rule_based_report(statistics, report_title=report_title)
            ai_summary = self._optional_ai_summary(statistics, rule_report)
            now = _utc_datetime(self.clock())
            record = DailyReportRecord(
                id=uuid4(),
                report_date=report_date,
                environment=environment,
                report_schema_version=REPORT_SCHEMA_VERSION,
                statistics=statistics.model_dump(mode="json"),
                rule_based_report=rule_report.model_dump(mode="json"),
                ai_summary=(ai_summary.model_dump(mode="json") if ai_summary is not None else None),
                summary_provider=(
                    self.summary_provider.name
                    if ai_summary is not None and self.summary_provider is not None
                    else None
                ),
                delivery_key=make_report_delivery_key(report_date, environment),
                provider=self.notification_provider.name,
                status=NotificationDeliveryStatus.PENDING,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(DailyReportRecord).where(
                        DailyReportRecord.report_date == report_date,
                        DailyReportRecord.environment == environment,
                    )
                )
                if existing is None:
                    raise
                return existing.id, False
            return record.id, True

    def _optional_ai_summary(
        self,
        statistics: DailyStatistics,
        rule_report: RuleBasedDailyReport,
    ) -> DailyReportAISummary | None:
        if self.summary_provider is None:
            return None
        try:
            return self.summary_provider.summarize(statistics, rule_report)
        except DailyReportSummaryProviderError:
            logger.warning("daily report AI summary failed; using rule-based report")
            return None

    def _deliver(self, report_id: UUID) -> bool:
        provider_error: NotificationProviderError | None = None
        delivered = False
        now = _utc_datetime(self.clock())
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(DailyReportRecord).where(DailyReportRecord.id == report_id).with_for_update()
            )
            if record is None:  # pragma: no cover - database invariant
                raise RuntimeError("Daily report disappeared")
            if record.status == NotificationDeliveryStatus.DELIVERED:
                return False
            if not provider_names_overlap(record.provider, self.notification_provider.name):
                raise RuntimeError("Daily report notification Provider changed")

            payload = DailyReportNotificationPayload(
                statistics=DailyStatistics.model_validate(record.statistics),
                rule_based_report=RuleBasedDailyReport.model_validate(record.rule_based_report),
                ai_summary=(
                    DailyReportAISummary.model_validate(record.ai_summary)
                    if record.ai_summary is not None
                    else None
                ),
            )
            record.attempt_count += 1
            record.updated_at = now
            try:
                status_code = self.notification_provider.send(
                    payload,
                    delivery_key=record.delivery_key,
                )
            except NotificationProviderError as error:
                record.status = NotificationDeliveryStatus.FAILED
                record.last_error_category = error.category.value
                record.last_response_status = error.response_status
                provider_error = error
            else:
                record.status = NotificationDeliveryStatus.DELIVERED
                record.last_error_category = None
                record.last_response_status = status_code
                record.delivered_at = now
                delivered = True
        if provider_error is not None:
            raise provider_error
        return delivered


def make_report_delivery_key(report_date: date, environment: str) -> str:
    """Build one stable receiver idempotency key per UTC date and environment."""
    canonical = json.dumps(
        {
            "delivery_key_version": REPORT_DELIVERY_KEY_VERSION,
            "environment": environment,
            "notification_type": "DAGSENTRY_DAILY_REPORT",
            "report_date": report_date.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _format_duration(value: float | None) -> str:
    return "집계 없음" if value is None else f"{value:.1f}초"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Daily report clock must include a timezone")
    return value.astimezone(UTC)
