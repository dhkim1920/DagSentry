from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.daily_report import build_rule_based_report, make_report_delivery_key
from dagsentry.db import SessionFactory
from dagsentry.domain.notification import NotificationDeliveryStatus
from dagsentry.domain.reporting import DailyReportAISummary
from dagsentry.models import DailyReportRecord
from tests.test_daily_report import statistics
from tests.test_incident_api import VIEWER_HEADER, request


def add_report(
    session_factory: SessionFactory,
    *,
    report_date: date,
    environment: str,
    delivery_status: NotificationDeliveryStatus = NotificationDeliveryStatus.DELIVERED,
    ai_summary: bool = False,
) -> UUID:
    report_statistics = statistics().model_copy(
        update={"report_date": report_date, "environment": environment},
    )
    start = datetime.combine(report_date, datetime.min.time(), tzinfo=UTC)
    report_statistics = report_statistics.model_copy(
        update={"period_start": start, "period_end": start + timedelta(days=1)},
    )
    rule_report = build_rule_based_report(report_statistics)
    report_id = uuid4()
    created_at = start + timedelta(days=1, minutes=10)
    with session_factory.begin() as session:
        session.add(
            DailyReportRecord(
                id=report_id,
                report_date=report_date,
                environment=environment,
                report_schema_version=1,
                statistics=report_statistics.model_dump(mode="json"),
                rule_based_report=rule_report.model_dump(mode="json"),
                ai_summary=(
                    DailyReportAISummary(
                        key_changes=("Repeated network failures remain elevated.",),
                        priorities=("Review the unresolved network incident.",),
                    ).model_dump(mode="json")
                    if ai_summary
                    else None
                ),
                summary_provider="openai" if ai_summary else None,
                delivery_key=make_report_delivery_key(report_date, environment),
                provider="slack",
                status=delivery_status,
                attempt_count=1,
                last_response_status=200
                if delivery_status == NotificationDeliveryStatus.DELIVERED
                else 503,
                delivered_at=(
                    created_at if delivery_status == NotificationDeliveryStatus.DELIVERED else None
                ),
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return report_id


def test_daily_report_routes_require_authentication(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    listing = request(app, "GET", "/api/v1/daily-reports")
    detail = request(app, "GET", f"/api/v1/daily-reports/{uuid4()}")

    assert listing.status_code == 401
    assert detail.status_code == 401


def test_list_daily_reports_filters_and_paginates_newest_first(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    add_report(
        session_factory,
        report_date=date(2026, 8, 11),
        environment="production",
    )
    expected_id = add_report(
        session_factory,
        report_date=date(2026, 8, 12),
        environment="production",
        ai_summary=True,
    )
    add_report(
        session_factory,
        report_date=date(2026, 8, 13),
        environment="staging",
        delivery_status=NotificationDeliveryStatus.FAILED,
    )

    response = request(
        create_app(settings, session_factory),
        "GET",
        "/api/v1/daily-reports?environment=production&status=DELIVERED"
        "&date_from=2026-08-12&date_to=2026-08-12&limit=1&offset=0",
        headers=VIEWER_HEADER,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["items"][0]["id"] == str(expected_id)
    assert payload["items"][0]["statistics"]["failure_attempts"] == 3
    assert payload["items"][0]["ai_summary_used"] is True
    assert payload["items"][0]["provider"] == "slack"

    ai_only = request(
        create_app(settings, session_factory),
        "GET",
        "/api/v1/daily-reports?ai_summary_used=true",
        headers=VIEWER_HEADER,
    )
    assert ai_only.status_code == 200
    assert ai_only.json()["total"] == 1
    assert ai_only.json()["items"][0]["id"] == str(expected_id)


def test_get_daily_report_returns_stored_rule_and_ai_content(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    report_id = add_report(
        session_factory,
        report_date=date(2026, 8, 12),
        environment="production",
        ai_summary=True,
    )

    response = request(
        create_app(settings, session_factory),
        "GET",
        f"/api/v1/daily-reports/{report_id}",
        headers=VIEWER_HEADER,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rule_based_report"]["overview"].startswith("UTC 기준 실패 3회")
    assert payload["ai_summary"]["key_changes"] == ["Repeated network failures remain elevated."]
    assert payload["status"] == "DELIVERED"


def test_daily_report_query_validates_dates_and_missing_detail(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    invalid = request(
        app,
        "GET",
        "/api/v1/daily-reports?date_from=2026-08-13&date_to=2026-08-12",
        headers=VIEWER_HEADER,
    )
    missing = request(
        app,
        "GET",
        f"/api/v1/daily-reports/{uuid4()}",
        headers=VIEWER_HEADER,
    )

    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "date_from must not be after date_to"}
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Daily Report not found"}
