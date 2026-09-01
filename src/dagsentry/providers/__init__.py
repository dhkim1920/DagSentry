"""External Provider adapters and runtime selection."""

from typing import Protocol

from dagsentry.config import Settings
from dagsentry.domain.notification import NotificationPayload
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.llm import LLMProvider
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


class NotificationProviderAdapter(Protocol):
    """Runtime Provider shape shared by every notification-producing process."""

    @property
    def name(self) -> str:
        """Stable Provider name persisted with delivery state."""

    def send(
        self,
        payload: NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload,
        *,
        delivery_key: str,
    ) -> int:
        """Deliver any current DagSentry notification payload."""


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
    if settings.notification_provider == "discord":
        return DiscordNotificationProvider.from_settings(settings)
    if settings.notification_provider == "slack":
        return SlackNotificationProvider.from_settings(settings)
    if settings.notification_provider == "teams":
        return TeamsNotificationProvider.from_settings(settings)
    if settings.notification_provider == "smtp":
        return SMTPNotificationProvider.from_settings(settings)
    return WebhookNotificationProvider.from_settings(settings)
