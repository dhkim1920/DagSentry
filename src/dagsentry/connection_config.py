"""Allowlisted non-secret configuration for managed outbound connections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose
from dagsentry.outbound_urls import require_credential_endpoint_security

BoundedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
SecretValue = Annotated[SecretStr, Field(min_length=1, max_length=8192)]


class ConnectionConfigError(ValueError):
    """A Provider does not belong to a purpose or its config is invalid."""


class _RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=5.0, gt=0, le=300)
    max_attempts: int = Field(default=2, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=60)

    @model_validator(mode="after")
    def reject_secrets_in_urls(self) -> _RetryConfig:
        for value in self.__dict__.values():
            if isinstance(value, AnyUrl) and (
                value.username is not None
                or value.password is not None
                or value.query is not None
                or value.fragment is not None
            ):
                raise ValueError("non-secret URLs cannot contain credentials, query, or fragment")
        return self


class _CredentialEndpointConfig(_RetryConfig):
    """Configuration shared by providers that send a credential to an HTTP endpoint."""

    trusted_http_hosts: frozenset[BoundedName] = Field(default_factory=frozenset)


class AirflowConnectionConfig(_RetryConfig):
    """Non-secret Airflow Public API and UI settings."""

    api_base_url: AnyHttpUrl
    ui_base_url: AnyHttpUrl | None = None
    log_max_response_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)


class OllamaConnectionConfig(_RetryConfig):
    """Non-secret Ollama endpoint and model settings."""

    api_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434/api")
    model: BoundedName
    max_output_tokens: int = Field(default=2_048, ge=1, le=131_072)


class OpenAIConnectionConfig(_CredentialEndpointConfig):
    """Non-secret OpenAI endpoint and model settings."""

    api_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    model: BoundedName

    @model_validator(mode="after")
    def require_https(self) -> OpenAIConnectionConfig:
        require_credential_endpoint_security(
            str(self.api_base_url),
            provider="OpenAI API",
            trusted_http_hosts=self.trusted_http_hosts,
        )
        return self


class AzureOpenAIConnectionConfig(_RetryConfig):
    """Non-secret Azure OpenAI endpoint and deployment settings."""

    endpoint: AnyHttpUrl
    model: BoundedName

    @field_validator("endpoint")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("Azure OpenAI endpoint must use HTTPS")
        return value


class AnthropicConnectionConfig(_CredentialEndpointConfig):
    """Non-secret Anthropic endpoint and model settings."""

    api_base_url: AnyHttpUrl = AnyHttpUrl("https://api.anthropic.com/v1")
    model: BoundedName
    api_version: BoundedName = "2023-06-01"
    max_output_tokens: int = Field(default=2_048, ge=1, le=131_072)

    @model_validator(mode="after")
    def require_https(self) -> AnthropicConnectionConfig:
        require_credential_endpoint_security(
            str(self.api_base_url),
            provider="Anthropic API",
            trusted_http_hosts=self.trusted_http_hosts,
        )
        return self


class BedrockConnectionConfig(_RetryConfig):
    """Non-secret AWS Bedrock region and model settings."""

    region: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=64,
            pattern=r"^[a-z0-9-]+$",
        ),
    ]
    model_id: BoundedName
    max_output_tokens: int = Field(default=2_048, ge=1, le=131_072)


class WebhookConnectionConfig(_RetryConfig):
    """Non-secret Webhook delivery limits; URL and token are encrypted secrets."""


class SlackConnectionConfig(_CredentialEndpointConfig):
    """Non-secret Slack Web API endpoint and destination channel."""

    api_base_url: AnyHttpUrl = AnyHttpUrl("https://slack.com/api")
    channel: BoundedName

    @model_validator(mode="after")
    def require_https(self) -> SlackConnectionConfig:
        require_credential_endpoint_security(
            str(self.api_base_url),
            provider="Slack API",
            trusted_http_hosts=self.trusted_http_hosts,
        )
        return self


class TeamsConnectionConfig(_RetryConfig):
    """Non-secret Teams delivery limits; the Webhook URL is an encrypted secret."""


class DiscordConnectionConfig(_RetryConfig):
    """Non-secret Discord delivery limits; the Webhook URL is an encrypted secret."""


class _SecretConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AirflowSecretConfig(_SecretConfig):
    token: SecretValue


class APIKeySecretConfig(_SecretConfig):
    api_key: SecretValue


class BedrockSecretConfig(_SecretConfig):
    access_key_id: SecretValue
    secret_access_key: SecretValue
    session_token: SecretValue | None = None


class WebhookSecretConfig(_SecretConfig):
    url: SecretValue
    bearer_token: SecretValue | None = None

    @field_validator("url")
    @classmethod
    def require_http_url(cls, value: SecretStr) -> SecretStr:
        _require_secret_http_url(value)
        return value


class SlackSecretConfig(_SecretConfig):
    bot_token: SecretValue


class WebhookURLSecretConfig(_SecretConfig):
    webhook_url: SecretValue

    @field_validator("webhook_url")
    @classmethod
    def require_http_url(cls, value: SecretStr) -> SecretStr:
        _require_secret_http_url(value)
        return value


_PROVIDER_PURPOSE = {
    ConnectionProvider.AIRFLOW: ConnectionPurpose.AIRFLOW,
    ConnectionProvider.OLLAMA: ConnectionPurpose.LLM,
    ConnectionProvider.OPENAI: ConnectionPurpose.LLM,
    ConnectionProvider.AZURE_OPENAI: ConnectionPurpose.LLM,
    ConnectionProvider.ANTHROPIC: ConnectionPurpose.LLM,
    ConnectionProvider.BEDROCK: ConnectionPurpose.LLM,
    ConnectionProvider.WEBHOOK: ConnectionPurpose.NOTIFICATION,
    ConnectionProvider.SLACK: ConnectionPurpose.NOTIFICATION,
    ConnectionProvider.TEAMS: ConnectionPurpose.NOTIFICATION,
    ConnectionProvider.DISCORD: ConnectionPurpose.NOTIFICATION,
}

_CONFIG_MODELS: dict[ConnectionProvider, type[BaseModel]] = {
    ConnectionProvider.AIRFLOW: AirflowConnectionConfig,
    ConnectionProvider.OLLAMA: OllamaConnectionConfig,
    ConnectionProvider.OPENAI: OpenAIConnectionConfig,
    ConnectionProvider.AZURE_OPENAI: AzureOpenAIConnectionConfig,
    ConnectionProvider.ANTHROPIC: AnthropicConnectionConfig,
    ConnectionProvider.BEDROCK: BedrockConnectionConfig,
    ConnectionProvider.WEBHOOK: WebhookConnectionConfig,
    ConnectionProvider.SLACK: SlackConnectionConfig,
    ConnectionProvider.TEAMS: TeamsConnectionConfig,
    ConnectionProvider.DISCORD: DiscordConnectionConfig,
}

_SECRET_MODELS: dict[ConnectionProvider, type[_SecretConfig] | None] = {
    ConnectionProvider.AIRFLOW: AirflowSecretConfig,
    ConnectionProvider.OLLAMA: None,
    ConnectionProvider.OPENAI: APIKeySecretConfig,
    ConnectionProvider.AZURE_OPENAI: APIKeySecretConfig,
    ConnectionProvider.ANTHROPIC: APIKeySecretConfig,
    ConnectionProvider.BEDROCK: BedrockSecretConfig,
    ConnectionProvider.WEBHOOK: WebhookSecretConfig,
    ConnectionProvider.SLACK: SlackSecretConfig,
    ConnectionProvider.TEAMS: WebhookURLSecretConfig,
    ConnectionProvider.DISCORD: WebhookURLSecretConfig,
}


def validate_non_secret_config(
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
    config: Mapping[str, object],
) -> dict[str, object]:
    """Validate and canonicalize only the fields allowlisted for one Provider."""
    expected_purpose = _PROVIDER_PURPOSE[provider]
    if purpose != expected_purpose:
        raise ConnectionConfigError(
            f"{provider.value} is not a valid {purpose.value} connection provider"
        )
    try:
        validated = _CONFIG_MODELS[provider].model_validate(dict(config))
    except ValidationError as error:
        raise ConnectionConfigError(f"non-secret config is invalid for {provider.value}") from error
    return validated.model_dump(mode="json", exclude_none=True)


def validate_secret_config(
    provider: ConnectionProvider,
    config: object,
) -> dict[str, str]:
    """Validate one write-only Provider Secret without echoing invalid input."""
    model = _SECRET_MODELS[provider]
    if model is None:
        raise ConnectionConfigError(f"{provider.value} does not accept a Secret")
    if not isinstance(config, Mapping):
        raise ConnectionConfigError(f"Secret config is invalid for {provider.value}")
    try:
        validated = model.model_validate(dict(config))
    except ValidationError as error:
        raise ConnectionConfigError(f"Secret config is invalid for {provider.value}") from error
    secret: dict[str, str] = {}
    for field, value in validated:
        if value is not None:
            secret[field] = value.get_secret_value()
    return secret


def _require_secret_http_url(value: SecretStr) -> None:
    parsed = urlsplit(value.get_secret_value())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Secret URL must be an HTTP(S) URL")
