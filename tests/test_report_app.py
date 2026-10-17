import sys
from unittest.mock import patch

import pytest

from dagsentry.db import SessionFactory
from dagsentry.providers.openai_report import OpenAIReportSummaryProvider
from dagsentry.report_app import _report_summary_provider, main
from tests.test_managed_provider_runtime import add_connection, database_settings


def test_invalid_cli_timezone_precedes_engine_and_provider_creation() -> None:
    with (
        patch.object(sys, "argv", ["dagsentry-daily-report", "--timezone", "Not/A_Timezone"]),
        patch("dagsentry.report_app.create_session_factory") as factory,
        patch("dagsentry.report_app.create_report_summary_provider") as provider,
    ):
        with pytest.raises(SystemExit) as error:
            main()
    assert error.value.code == 2
    factory.assert_not_called()
    provider.assert_not_called()


def test_report_summary_uses_database_connection_without_environment_fallback(
    session_factory: SessionFactory,
) -> None:
    settings = database_settings()
    add_connection(session_factory, settings, "OPENAI")
    summary = _report_summary_provider(settings, session_factory, "test")
    assert isinstance(summary, OpenAIReportSummaryProvider)
    assert summary.config.api_key == "database-api-secret"
    assert summary.config.model == "database-model"
    assert summary.config.prompt_version == settings.daily_report_prompt_version
    assert _report_summary_provider(settings, session_factory, "missing") is None
