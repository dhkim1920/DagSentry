"""Daily report process entry point."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from dagsentry.config import Settings, get_settings
from dagsentry.daily_report import DailyReportRunResult, DailyReportService
from dagsentry.db import SessionFactory, create_session_factory
from dagsentry.domain.reporting import DailyReportSummaryProvider, validate_timezone
from dagsentry.observability import configure_logging
from dagsentry.providers import create_report_summary_provider
from dagsentry.providers.anthropic import AnthropicProvider
from dagsentry.providers.anthropic_report import AnthropicReportSummaryProvider
from dagsentry.providers.azure_openai import AzureOpenAIProvider
from dagsentry.providers.bedrock import BedrockProvider
from dagsentry.providers.bedrock_report import BedrockReportSummaryProvider
from dagsentry.providers.openai import OpenAIProvider
from dagsentry.providers.openai_report import OpenAIReportSummaryProvider
from dagsentry.runtime_connections import (
    ConfiguredLLMConnectionResolver,
    ConfiguredNotificationConnectionResolver,
    RuntimeConnectionError,
)

logger = logging.getLogger(__name__)


def _report_summary_provider(
    settings: Settings, session_factory: SessionFactory, environment: str
) -> DailyReportSummaryProvider | None:
    if settings.llm_config_source == "environment":
        return create_report_summary_provider(settings)
    try:
        provider = (
            ConfiguredLLMConnectionResolver(
                settings.model_copy(
                    update={"llm_prompt_version": settings.daily_report_prompt_version}
                ),
                session_factory,
            )
            .resolve(environment)
            .provider
        )
    except RuntimeConnectionError:
        logger.warning("report_summary_connection_unavailable")
        return None
    if isinstance(provider, OpenAIProvider) and not isinstance(provider, AzureOpenAIProvider):
        return OpenAIReportSummaryProvider(provider.config, provider.http_client)
    if isinstance(provider, AnthropicProvider):
        return AnthropicReportSummaryProvider(provider.config, provider.http_client)
    if isinstance(provider, BedrockProvider):
        return BedrockReportSummaryProvider(provider.config, provider.client)
    return None


def run_daily_report(
    report_date: date,
    settings: Settings,
    *,
    environment: str | None = None,
    notification_connection_id: UUID | None = None,
    use_ai_summary: bool = True,
    report_title: str | None = None,
    timezone: str | None = None,
    session_factory: SessionFactory | None = None,
) -> DailyReportRunResult:
    """Build configured Providers and execute one report date."""
    report_timezone = validate_timezone(timezone or settings.report_timezone)
    session_factory = session_factory or create_session_factory(settings.database_url)
    report_environment = environment or settings.environment
    summary_provider = (
        _report_summary_provider(settings, session_factory, report_environment)
        if use_ai_summary
        else None
    )
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
        timezone=report_timezone,
    )
    return service.run(report_date, report_environment, report_title=report_title)


def main() -> None:
    """Generate and deliver one UTC daily report."""
    parser = argparse.ArgumentParser(description="Generate one DagSentry UTC daily report")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Local report date in YYYY-MM-DD form (default: yesterday)",
    )
    parser.add_argument("--timezone", type=validate_timezone, help="IANA report timezone")
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging("daily-report", settings.log_level)
    timezone = arguments.timezone or settings.report_timezone
    report_date = arguments.date or datetime.now(ZoneInfo(timezone)).date() - timedelta(days=1)
    result = run_daily_report(report_date, settings, timezone=timezone)
    print(json.dumps(asdict(result), sort_keys=True, default=str))
