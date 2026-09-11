"""Versioned daily Statistics JSON contract."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dagsentry.domain.diagnosis import ErrorClassification
from dagsentry.domain.incident import IncidentStatus

STATISTICS_SCHEMA_VERSION: Literal[2] = 2
REPORT_SCHEMA_VERSION: Literal[2] = 2


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("timezone must be an IANA timezone") from error
    return value


def report_period(report_date: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(validate_timezone(timezone))
    start = datetime.combine(report_date, time.min, tzinfo=zone)
    end = datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


class TopFailure(BaseModel):
    """One DAG/Task ranked by failed attempt count in the selected period."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    dag_id: str
    task_id: str
    failure_count: int = Field(ge=1)
    last_failed_at: datetime
    classification: ErrorClassification | None = None
    incident_id: UUID | None = None
    incident_status: IncidentStatus | None = None
    root_cause: str | None = None


class IncidentStatistics(BaseModel):
    """Incident counts observed in or at the end of one UTC period."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    new: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    recovered: int = Field(ge=0)


class ErrorSignatureStatistics(BaseModel):
    """First-seen versus previously-seen Error Signatures in one environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    new: int = Field(ge=0)
    repeated: int = Field(ge=0)


class MeanTimeStatistics(BaseModel):
    """Mean lifecycle durations for transitions occurring in the report period."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recovery_seconds: float | None = Field(default=None, ge=0)
    resolution_seconds: float | None = Field(default=None, ge=0)


class DailyStatistics(BaseModel):
    """Complete deterministic input for a version 1 daily report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = STATISTICS_SCHEMA_VERSION
    report_date: date
    timezone: str = "UTC"
    period_start: datetime
    period_end: datetime
    environment: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    failure_attempts: int = Field(ge=0)
    affected_task_instances: int = Field(ge=0)
    affected_dag_runs: int = Field(ge=0)
    incidents: IncidentStatistics
    error_signatures: ErrorSignatureStatistics
    classification_counts: dict[ErrorClassification, int]
    mean_time: MeanTimeStatistics
    top_failures: tuple[TopFailure, ...] = Field(default=(), max_length=20)

    _timezone = field_validator("timezone")(validate_timezone)

    @model_validator(mode="after")
    def validate_complete_period_and_classifications(self) -> Self:
        if self.period_start.tzinfo is None or self.period_start.utcoffset() is None:
            raise ValueError("period_start must include a timezone")
        if self.period_end.tzinfo is None or self.period_end.utcoffset() is None:
            raise ValueError("period_end must include a timezone")
        expected_start, expected_end = report_period(self.report_date, self.timezone)
        if self.period_start != expected_start or self.period_start.utcoffset() != timedelta(0):
            raise ValueError("period_start must be local report_date midnight converted to UTC")
        if self.period_end != expected_end or self.period_end.utcoffset() != timedelta(0):
            raise ValueError("period_end must be next local midnight converted to UTC")
        if set(self.classification_counts) != set(ErrorClassification):
            raise ValueError("classification_counts must contain every classification")
        if any(count < 0 for count in self.classification_counts.values()):
            raise ValueError("classification counts must not be negative")
        return self


class RuleBasedDailyReport(BaseModel):
    """Complete deterministic prose derived only from Daily Statistics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    overview: str = Field(min_length=1)
    highlights: tuple[str, ...] = Field(min_length=1)
    priorities: tuple[str, ...] = Field(min_length=1)


class DailyReportAISummary(BaseModel):
    """Optional narrative fields that cannot replace calculated Statistics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key_changes: tuple[str, ...] = Field(min_length=1, max_length=5)
    priorities: tuple[str, ...] = Field(min_length=1, max_length=5)


class DailyReportNotificationPayload(BaseModel):
    """Provider-neutral daily report payload with immutable Statistics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_type: Literal["DAGSENTRY_DAILY_REPORT"] = "DAGSENTRY_DAILY_REPORT"
    report_schema_version: Literal[2] = REPORT_SCHEMA_VERSION
    statistics: DailyStatistics
    rule_based_report: RuleBasedDailyReport
    ai_summary: DailyReportAISummary | None = None


class DailyReportSummaryProviderError(RuntimeError):
    """A sanitized optional AI summary failure safe for fallback handling."""


class DailyReportSummaryProvider(Protocol):
    """Optional AI boundary restricted to narrative output."""

    @property
    def name(self) -> str:
        """Stable Provider name stored with the generated narrative."""

    def summarize(
        self,
        statistics: DailyStatistics,
        rule_based_report: RuleBasedDailyReport,
    ) -> DailyReportAISummary:
        """Explain supplied Statistics without returning replacement numeric fields."""


class DailyReportNotificationProvider(Protocol):
    """Delivery boundary used by the daily report service."""

    @property
    def name(self) -> str:
        """Stable Provider name stored with delivery attempts."""

    def send(self, payload: DailyReportNotificationPayload, *, delivery_key: str) -> int:
        """Deliver one daily report using a stable idempotency key."""
