"""Azure OpenAI v1 Responses API adapter for structured AI Diagnosis."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from dagsentry.config import Settings
from dagsentry.providers.openai import OpenAIProvider, OpenAIProviderConfig


@dataclass(frozen=True)
class AzureOpenAIProviderConfig(OpenAIProviderConfig):
    """Azure OpenAI endpoint, deployment name, API key, and retry limits."""

    def __post_init__(self) -> None:
        super().__post_init__()
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Azure OpenAI endpoint must use HTTPS")

    def __repr__(self) -> str:
        return (
            "AzureOpenAIProviderConfig(api_key='**********', "
            f"model={self.model!r}, prompt_version={self.prompt_version!r}, "
            f"base_url={self.base_url!r}, timeout_seconds={self.timeout_seconds!r}, "
            f"max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class AzureOpenAIProvider(OpenAIProvider):
    """Use Azure authentication while preserving the neutral LLM contract."""

    provider_name = "Azure OpenAI"

    @classmethod
    def from_settings(cls, settings: Settings) -> AzureOpenAIProvider:
        """Build the explicitly configured Azure OpenAI Provider."""
        if settings.llm_provider != "azure_openai":
            raise ValueError("Azure OpenAI LLM Provider is not enabled")
        if (
            settings.azure_openai_endpoint is None
            or settings.azure_openai_api_key is None
            or settings.llm_model is None
        ):
            raise ValueError("Azure OpenAI endpoint, API key, and deployment must be configured")
        return cls(
            AzureOpenAIProviderConfig(
                api_key=settings.azure_openai_api_key.get_secret_value(),
                model=settings.llm_model,
                prompt_version=settings.llm_prompt_version,
                base_url=_v1_base_url(settings.azure_openai_endpoint),
                timeout_seconds=settings.llm_timeout_seconds,
                max_attempts=settings.llm_max_attempts,
                retry_backoff_seconds=settings.llm_retry_backoff_seconds,
            )
        )

    def _request_headers(self) -> dict[str, str]:
        return {
            "api-key": self.config.api_key,
            "Content-Type": "application/json",
        }


def _v1_base_url(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/openai/v1"):
        return normalized
    return normalized + "/openai/v1"
