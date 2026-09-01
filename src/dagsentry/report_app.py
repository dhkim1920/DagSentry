"""Daily report process entry point."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from dagsentry.config import Settings, get_settings
from dagsentry.daily_report import DailyReportRunResult, DailyReportService
from dagsentry.db import create_session_factory
from dagsentry.observability import configure_logging
from dagsentry.providers.openai_report import OpenAIReportSummaryProvider
from dagsentry.runtime_connections import ConfiguredNotificationConnectionResolver


def run_daily_report(
    report_date: date,
    settings: Settings,
    *,
    environment: str | None = None,
    notification_connection_id: UUID | None = None,
    use_ai_summary: bool = True,
    report_title: str | None = None,
) -> DailyReportRunResult:
    """Build configured Providers and execute one report date."""
    summary_provider = (
        OpenAIReportSummaryProvider.from_settings(settings)
        if use_ai_summary and settings.llm_provider == "openai"
        else None
    )
    session_factory = create_session_factory(settings.database_url)
    report_environment = environment or settings.environment
    notification_provider = (
        ConfiguredNotificationConnectionResolver(
            settings,
            session_factory,
        )
        .resolve(report_environment, connection_id=notification_connection_id)
        .provider
    )
    service = DailyReportService(
        session_factory,
        notification_provider=notification_provider,
        summary_provider=summary_provider,
    )
    return service.run(report_date, report_environment, report_title=report_title)


def main() -> None:
    """Generate and deliver one UTC daily report."""
    parser = argparse.ArgumentParser(description="Generate one DagSentry UTC daily report")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date() - timedelta(days=1),
        help="UTC report date in YYYY-MM-DD form (default: yesterday)",
    )
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging("daily-report", settings.log_level)
    result = run_daily_report(arguments.date, settings)
    print(json.dumps(asdict(result), sort_keys=True, default=str))
