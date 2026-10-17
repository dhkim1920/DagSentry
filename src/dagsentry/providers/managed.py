"""Build built-in adapters exclusively from one decrypted Managed Connection."""

from __future__ import annotations

from typing import Any

import httpx

from dagsentry.connection_config import (
    AnthropicConnectionConfig,
    AzureOpenAIConnectionConfig,
    BedrockConnectionConfig,
    OllamaConnectionConfig,
    OpenAIConnectionConfig,
    SlackConnectionConfig,
    SMTPConnectionConfig,
    validate_non_secret_config,
    validate_secret_config,
)
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.llm import LLMProvider
from dagsentry.providers.anthropic import AnthropicProvider, AnthropicProviderConfig
from dagsentry.providers.azure_openai import (
    AzureOpenAIProvider,
    AzureOpenAIProviderConfig,
    _v1_base_url,
)
from dagsentry.providers.bedrock import BedrockProvider, BedrockProviderConfig, _create_client
from dagsentry.providers.discord import DiscordNotificationProvider, DiscordProviderConfig
from dagsentry.providers.notification_adapter import NotificationProviderAdapter
from dagsentry.providers.ollama import OllamaProvider, OllamaProviderConfig
from dagsentry.providers.openai import OpenAIProvider, OpenAIProviderConfig
from dagsentry.providers.slack import SlackNotificationProvider, SlackProviderConfig
from dagsentry.providers.smtp import SMTPNotificationProvider, SMTPProviderConfig
from dagsentry.providers.teams import TeamsNotificationProvider, TeamsProviderConfig
from dagsentry.providers.webhook import WebhookNotificationProvider, WebhookProviderConfig


def create_managed_llm_provider(
    provider: ConnectionProvider,
    config: dict[str, object],
    secret: dict[str, str],
    *,
    prompt_version: str,
    http_client: httpx.Client,
) -> LLMProvider:
    """Validate persisted values again and never use environment credentials."""
    values = validate_non_secret_config(ConnectionPurpose.LLM, provider, config)
    retry: dict[str, Any] = {
        key: values[key] for key in ("timeout_seconds", "max_attempts", "retry_backoff_seconds")
    }
    if provider == ConnectionProvider.OLLAMA:
        ollama = OllamaConnectionConfig.model_validate(values)
        return OllamaProvider(
            OllamaProviderConfig(
                model=ollama.model,
                prompt_version=prompt_version,
                base_url=str(ollama.api_base_url),
                max_output_tokens=ollama.max_output_tokens,
                **retry,
            ),
            http_client,
        )
    secret = validate_secret_config(provider, secret)
    if provider == ConnectionProvider.OPENAI:
        openai = OpenAIConnectionConfig.model_validate(values)
        return OpenAIProvider(
            OpenAIProviderConfig(
                api_key=secret["api_key"],
                model=openai.model,
                prompt_version=prompt_version,
                base_url=str(openai.api_base_url),
                trusted_http_hosts=openai.trusted_http_hosts,
                **retry,
            ),
            http_client,
        )
    if provider == ConnectionProvider.AZURE_OPENAI:
        azure = AzureOpenAIConnectionConfig.model_validate(values)
        return AzureOpenAIProvider(
            AzureOpenAIProviderConfig(
                api_key=secret["api_key"],
                model=azure.model,
                prompt_version=prompt_version,
                base_url=_v1_base_url(str(azure.endpoint)),
                **retry,
            ),
            http_client,
        )
    if provider == ConnectionProvider.ANTHROPIC:
        anthropic = AnthropicConnectionConfig.model_validate(values)
        return AnthropicProvider(
            AnthropicProviderConfig(
                api_key=secret["api_key"],
                model=anthropic.model,
                prompt_version=prompt_version,
                base_url=str(anthropic.api_base_url),
                api_version=anthropic.api_version,
                max_output_tokens=anthropic.max_output_tokens,
                trusted_http_hosts=anthropic.trusted_http_hosts,
                **retry,
            ),
            http_client,
        )
    bedrock = BedrockConnectionConfig.model_validate(values)
    bedrock_config = BedrockProviderConfig(
        region=bedrock.region,
        model_id=bedrock.model_id,
        prompt_version=prompt_version,
        max_output_tokens=bedrock.max_output_tokens,
        **retry,
    )
    credentials = {
        "aws_access_key_id": secret["access_key_id"],
        "aws_secret_access_key": secret["secret_access_key"],
    }
    if "session_token" in secret:
        credentials["aws_session_token"] = secret["session_token"]
    return BedrockProvider(bedrock_config, _create_client(bedrock_config, credentials=credentials))


def create_managed_notification_provider(
    provider: ConnectionProvider,
    config: dict[str, object],
    secret: dict[str, str],
    *,
    display_timezone: str,
    http_client: httpx.Client,
) -> NotificationProviderAdapter:
    """Build one notification target; connection selection has already happened."""
    values = validate_non_secret_config(ConnectionPurpose.NOTIFICATION, provider, config)
    retry: dict[str, Any] = {
        key: values[key] for key in ("timeout_seconds", "max_attempts", "retry_backoff_seconds")
    }
    if provider == ConnectionProvider.SMTP:
        smtp = SMTPConnectionConfig.model_validate(values)
        credentials = validate_secret_config(provider, secret) if secret else {}
        return SMTPNotificationProvider(
            SMTPProviderConfig(
                host=smtp.host,
                port=smtp.port,
                sender=smtp.sender,
                recipients=tuple(smtp.recipients),
                username=credentials.get("username"),
                password=credentials.get("password"),
                use_starttls=smtp.use_starttls,
                use_ssl=smtp.use_ssl,
                display_timezone=display_timezone,
                **retry,
            )
        )
    secret = validate_secret_config(provider, secret)
    if provider == ConnectionProvider.SLACK:
        slack = SlackConnectionConfig.model_validate(values)
        return SlackNotificationProvider(
            SlackProviderConfig(
                bot_token=secret["bot_token"],
                channel=slack.channel,
                base_url=str(slack.api_base_url),
                trusted_http_hosts=slack.trusted_http_hosts,
                **retry,
            ),
            http_client,
        )
    if provider == ConnectionProvider.WEBHOOK:
        return WebhookNotificationProvider(
            WebhookProviderConfig(
                url=secret["url"],
                bearer_token=secret.get("bearer_token"),
                **retry,
            ),
            http_client,
        )
    if provider == ConnectionProvider.TEAMS:
        return TeamsNotificationProvider(
            TeamsProviderConfig(
                webhook_url=secret["webhook_url"],
                display_timezone=display_timezone,
                **retry,
            ),
            http_client,
        )
    return DiscordNotificationProvider(
        DiscordProviderConfig(
            webhook_url=secret["webhook_url"],
            **retry,
        ),
        http_client,
    )
