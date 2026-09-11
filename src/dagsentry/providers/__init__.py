"""External Provider adapters and runtime selection."""

from dagsentry.config import Settings
from dagsentry.domain.reporting import DailyReportSummaryProvider
from dagsentry.llm import LLMProvider
from dagsentry.providers.anthropic import AnthropicProvider
from dagsentry.providers.anthropic_report import AnthropicReportSummaryProvider
from dagsentry.providers.azure_openai import AzureOpenAIProvider
from dagsentry.providers.bedrock import BedrockProvider
from dagsentry.providers.bedrock_report import BedrockReportSummaryProvider
from dagsentry.providers.discord import DiscordNotificationProvider
from dagsentry.providers.fallback import FallbackNotificationProvider
from dagsentry.providers.notification_adapter import (
    NotificationProviderAdapter as NotificationProviderAdapter,
)
from dagsentry.providers.ollama import OllamaProvider
from dagsentry.providers.openai import OpenAIProvider
from dagsentry.providers.openai_report import OpenAIReportSummaryProvider
from dagsentry.providers.slack import SlackNotificationProvider
from dagsentry.providers.smtp import SMTPNotificationProvider
from dagsentry.providers.teams import TeamsNotificationProvider
from dagsentry.providers.webhook import WebhookNotificationProvider


def create_report_summary_provider(settings: Settings) -> DailyReportSummaryProvider | None:
    if settings.llm_provider == "openai":
        return OpenAIReportSummaryProvider.from_settings(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicReportSummaryProvider.from_settings(settings)
    if settings.llm_provider == "bedrock":
        return BedrockReportSummaryProvider.from_settings(settings)
    return None


def create_llm_provider(settings: Settings) -> LLMProvider | None:
    """Return the explicitly enabled Provider or Rule-only mode."""
    if settings.llm_provider is None:
        return None
    if settings.llm_provider == "ollama":
        return OllamaProvider.from_settings(settings)
    if settings.llm_provider == "bedrock":
        return BedrockProvider.from_settings(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicProvider.from_settings(settings)
    if settings.llm_provider == "azure_openai":
        return AzureOpenAIProvider.from_settings(settings)
    return OpenAIProvider.from_settings(settings)


def create_notification_provider(settings: Settings) -> NotificationProviderAdapter:
    """Build the selected Notification Provider at the composition boundary."""
    primary = _create_notification_provider(settings)
    if not settings.notification_fallback_providers:
        return primary
    return FallbackNotificationProvider(
        (
            primary,
            *(
                _create_notification_provider(
                    settings.model_copy(update={"notification_provider": name})
                )
                for name in settings.notification_fallback_providers
            ),
        )
    )


def _create_notification_provider(settings: Settings) -> NotificationProviderAdapter:
    if settings.notification_provider == "discord":
        return DiscordNotificationProvider.from_settings(settings)
    if settings.notification_provider == "slack":
        return SlackNotificationProvider.from_settings(settings)
    if settings.notification_provider == "teams":
        return TeamsNotificationProvider.from_settings(settings)
    if settings.notification_provider == "smtp":
        return SMTPNotificationProvider.from_settings(settings)
    return WebhookNotificationProvider.from_settings(settings)
