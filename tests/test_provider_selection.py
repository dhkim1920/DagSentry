from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import SecretStr
from sqlalchemy import Column, String

from dagsentry.config import Settings
from dagsentry.models import (
    DailyReportRecord,
    IncidentRecoveryNotificationRecord,
    NotificationDeliveryRecord,
)
from dagsentry.providers import create_llm_provider, create_notification_provider
from dagsentry.providers.anthropic import AnthropicProvider
from dagsentry.providers.azure_openai import AzureOpenAIProvider
from dagsentry.providers.bedrock import BedrockProvider
from dagsentry.providers.discord import DiscordNotificationProvider
from dagsentry.providers.ollama import OllamaProvider
from dagsentry.providers.openai import OpenAIProvider
from dagsentry.providers.slack import SlackNotificationProvider
from dagsentry.providers.smtp import SMTPNotificationProvider
from dagsentry.providers.teams import TeamsNotificationProvider
from dagsentry.providers.webhook import WebhookNotificationProvider


@pytest.mark.parametrize(
    ("settings", "expected_type"),
    [
        (
            Settings(
                llm_provider="openai",
                llm_model="model",
                openai_api_key=SecretStr("secret"),
            ),
            OpenAIProvider,
        ),
        (
            Settings(
                llm_provider="azure_openai",
                llm_model="deployment",
                azure_openai_endpoint="https://example.openai.azure.com",
                azure_openai_api_key=SecretStr("secret"),
            ),
            AzureOpenAIProvider,
        ),
        (
            Settings(
                llm_provider="anthropic",
                llm_model="model",
                anthropic_api_key=SecretStr("secret"),
            ),
            AnthropicProvider,
        ),
        (
            Settings(
                llm_provider="bedrock",
                llm_model="model",
                bedrock_region="ap-northeast-2",
            ),
            BedrockProvider,
        ),
        (
            Settings(llm_provider="ollama", llm_model="model"),
            OllamaProvider,
        ),
    ],
)
def test_llm_provider_selection_requires_only_settings(
    settings: Settings,
    expected_type: type[object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "dagsentry.providers.bedrock._create_client",
        lambda _config: object(),
    )

    provider = create_llm_provider(settings)

    assert provider is not None
    assert isinstance(provider, expected_type)
    assert callable(provider.diagnose)


@pytest.mark.parametrize(
    ("settings", "expected_type", "expected_name"),
    [
        (
            Settings(webhook_url="https://example.test/dagsentry"),
            WebhookNotificationProvider,
            "webhook",
        ),
        (
            Settings(
                notification_provider="slack",
                slack_bot_token=SecretStr("secret"),
                slack_channel="C0123456789",
            ),
            SlackNotificationProvider,
            "slack",
        ),
        (
            Settings(
                notification_provider="teams",
                teams_webhook_url="https://teams.test/workflows/trigger?sig=secret",
            ),
            TeamsNotificationProvider,
            "teams",
        ),
        (
            Settings(
                notification_provider="discord",
                discord_webhook_url="https://discord.test/api/webhooks/id/token",
            ),
            DiscordNotificationProvider,
            "discord",
        ),
        (
            Settings(
                notification_provider="smtp",
                smtp_host="smtp.example.test",
                smtp_from="alerts@example.test",
                smtp_to=["oncall@example.test"],
            ),
            SMTPNotificationProvider,
            "smtp",
        ),
    ],
)
def test_notification_provider_selection_requires_only_settings(
    settings: Settings,
    expected_type: type[object],
    expected_name: str,
) -> None:
    provider = create_notification_provider(settings)

    assert isinstance(provider, expected_type)
    assert provider.name == expected_name
    send: Callable[..., int] = provider.send
    assert callable(send)


@pytest.mark.parametrize(
    "column",
    [
        NotificationDeliveryRecord.__table__.c.provider,
        IncidentRecoveryNotificationRecord.__table__.c.provider,
        DailyReportRecord.__table__.c.provider,
        DailyReportRecord.__table__.c.summary_provider,
    ],
)
def test_persisted_provider_names_do_not_require_vendor_specific_schema(
    column: Column[object],
) -> None:
    """New Provider names fit existing neutral columns without a DB migration."""
    assert isinstance(column.type, String)
