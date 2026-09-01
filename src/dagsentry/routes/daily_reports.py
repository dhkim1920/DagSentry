"""Authenticated Daily Report query routes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from dagsentry.db import get_session
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.domain.reporting import (
    DailyReportAISummary,
    DailyStatistics,
    RuleBasedDailyReport,
)
from dagsentry.models import DailyReportRecord
from dagsentry.security import authenticate_query_principal

router = APIRouter(
    prefix="/api/v1/daily-reports",
    tags=["daily-reports"],
    dependencies=[Depends(authenticate_query_principal)],
)


class DailyReportSummaryResponse(BaseModel):
    """One Daily Report list row with its immutable Statistics."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    report_date: date
    environment: str
    report_schema_version: int
    statistics: DailyStatistics
    title: str
    ai_summary_used: bool
    summary_provider: str | None
    provider: str
    status: NotificationDeliveryStatus
    attempt_count: int
    last_error_category: str | None
    last_response_status: int | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DailyReportListResponse(BaseModel):
    """Bounded Daily Report list."""

    model_config = ConfigDict(extra="forbid")

    items: list[DailyReportSummaryResponse]
    total: int
    limit: int
    offset: int


class DailyReportDetailResponse(DailyReportSummaryResponse):
    """Stored deterministic and optional AI report content."""

    rule_based_report: RuleBasedDailyReport
    ai_summary: DailyReportAISummary | None


@router.get("", response_model=DailyReportListResponse)
def list_daily_reports(
    session: Annotated[Session, Depends(get_session)],
    environment: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    delivery_status: Annotated[
        NotificationDeliveryStatus | None,
        Query(alias="status"),
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    ai_summary_used: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DailyReportListResponse:
    """List Daily Reports using stable newest-first ordering."""
    _validate_date_order(date_from, date_to)
    filters: list[ColumnElement[bool]] = []
    if environment is not None:
        filters.append(DailyReportRecord.environment == environment)
    if delivery_status is not None:
        filters.append(DailyReportRecord.status == delivery_status)
    if date_from is not None:
        filters.append(DailyReportRecord.report_date >= date_from)
    if date_to is not None:
        filters.append(DailyReportRecord.report_date <= date_to)
    if ai_summary_used is not None:
        filters.append(
            DailyReportRecord.ai_summary.is_not(None)
            if ai_summary_used
            else DailyReportRecord.ai_summary.is_(None)
        )

    records = session.scalars(
        select(DailyReportRecord)
        .where(*filters)
        .order_by(DailyReportRecord.report_date.desc(), DailyReportRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = session.scalar(select(func.count()).select_from(DailyReportRecord).where(*filters)) or 0
    return DailyReportListResponse(
        items=[_summary_response(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=DailyReportDetailResponse)
def get_daily_report(
    report_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> DailyReportDetailResponse:
    """Return one stored Daily Report snapshot."""
    record = session.get(DailyReportRecord, report_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Daily Report not found",
        )
    summary = _summary_response(record)
    return DailyReportDetailResponse(
        **summary.model_dump(),
        rule_based_report=RuleBasedDailyReport.model_validate(record.rule_based_report),
        ai_summary=(
            DailyReportAISummary.model_validate(record.ai_summary)
            if record.ai_summary is not None
            else None
        ),
    )


def _summary_response(record: DailyReportRecord) -> DailyReportSummaryResponse:
    statistics = DailyStatistics.model_validate(record.statistics)
    rule_report = RuleBasedDailyReport.model_validate(record.rule_based_report)
    return DailyReportSummaryResponse(
        id=record.id,
        report_date=record.report_date,
        environment=record.environment,
        report_schema_version=record.report_schema_version,
        statistics=statistics,
        title=rule_report.title,
        ai_summary_used=record.ai_summary is not None,
        summary_provider=record.summary_provider,
        provider=record.provider,
        status=record.status,
        attempt_count=record.attempt_count,
        last_error_category=record.last_error_category,
        last_response_status=record.last_response_status,
        delivered_at=_as_utc(record.delivered_at) if record.delivered_at is not None else None,
        created_at=_as_utc(record.created_at),
        updated_at=_as_utc(record.updated_at),
    )


def _validate_date_order(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to",
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
