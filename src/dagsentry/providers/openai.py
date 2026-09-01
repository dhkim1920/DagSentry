"""OpenAI Responses API adapter for structured AI Diagnosis."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from dagsentry.config import Settings
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    LLMCallMetadata,
    LLMProviderError,
    LLMResult,
)
from dagsentry.outbound_urls import require_credential_endpoint_security

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIProviderConfig:
    """Connection and retry limits for the OpenAI Responses API."""

    api_key: str
    model: str
    prompt_version: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 15.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5
    trusted_http_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        require_credential_endpoint_security(
            self.base_url,
            provider="OpenAI API",
            trusted_http_hosts=self.trusted_http_hosts,
        )
        if not self.api_key or not self.model or not self.prompt_version:
            raise ValueError("OpenAI API key, model, and prompt version must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

    def __repr__(self) -> str:
        return (
            "OpenAIProviderConfig(api_key='**********', "
            f"model={self.model!r}, prompt_version={self.prompt_version!r}, "
            f"base_url={self.base_url!r}, timeout_seconds={self.timeout_seconds!r}, "
            f"max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class OpenAIProvider:
    """Call the OpenAI Responses API without exposing its types to Core."""

    provider_name = "OpenAI"

    def __init__(
        self,
        config: OpenAIProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep
        self.monotonic = monotonic

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIProvider:
        """Build the configured v0.1 Provider or reject incomplete settings."""
        if settings.llm_provider != "openai":
            raise ValueError("OpenAI LLM Provider is not enabled")
        if settings.openai_api_key is None or settings.llm_model is None:
            raise ValueError("OpenAI API key and LLM model must be configured")
        return cls(
            OpenAIProviderConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.llm_model,
                prompt_version=settings.llm_prompt_version,
                base_url=settings.openai_api_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_attempts=settings.llm_max_attempts,
                retry_backoff_seconds=settings.llm_retry_backoff_seconds,
                trusted_http_hosts=settings.insecure_http_trusted_hosts,
            )
        )

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        """Request and parse one strict structured Diagnosis."""
        started_at = self.monotonic()
        for attempt in range(self.config.max_attempts):
            try:
                response = self.http_client.post(
                    f"{self.config.base_url.rstrip('/')}/responses",
                    headers=self._request_headers(),
                    json=self._request_body(request),
                    timeout=self.config.timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(None, started_at, "transport_error")
                raise LLMProviderError(f"{self.provider_name} request failed after retry") from None

            request_id = response.headers.get("x-request-id")
            if response.status_code in {408, 429} or response.status_code >= 500:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(request_id, started_at, f"http_{response.status_code}")
                raise LLMProviderError(f"{self.provider_name} request failed after retry")
            if response.status_code >= 400:
                self._log_failure(request_id, started_at, f"http_{response.status_code}")
                raise LLMProviderError(
                    f"{self.provider_name} request failed with HTTP {response.status_code}"
                )
            return self._parse_response(response, request_id, started_at)

        raise RuntimeError("unreachable OpenAI request state")  # pragma: no cover

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _request_body(self, request: AIDiagnosisRequest) -> dict[str, object]:
        return {
            "model": self.config.model,
            "store": False,
            "instructions": (
                "Diagnose the Airflow Task failure using only the supplied JSON context. "
                "Every evidence item must copy one supplied excerpt line_id and text exactly."
            ),
            "input": json.dumps(
                request.to_json_value(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "dagsentry_ai_diagnosis",
                    "strict": True,
                    "schema": AIDiagnosisResponse.model_json_schema(),
                }
            },
        }

    def _parse_response(
        self,
        response: httpx.Response,
        request_id: str | None,
        started_at: float,
    ) -> LLMResult:
        try:
            body: dict[str, Any] = response.json()
            output_text = _find_output_text(body)
            diagnosis = AIDiagnosisResponse.model_validate_json(output_text)
            usage = body.get("usage") or {}
            metadata = LLMCallMetadata(
                request_id=request_id or _optional_string(body.get("id")),
                model=_optional_string(body.get("model")) or self.config.model,
                prompt_version=self.config.prompt_version,
                latency_ms=_latency_ms(self.monotonic(), started_at),
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
            )
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ):
            self._log_failure(request_id, started_at, "invalid_response")
            raise LLMProviderError(
                f"{self.provider_name} returned an invalid structured response"
            ) from None

        logger.info(
            "LLM request completed request_id=%s model=%s prompt_version=%s "
            "latency_ms=%d input_tokens=%s output_tokens=%s",
            metadata.request_id,
            metadata.model,
            metadata.prompt_version,
            metadata.latency_ms,
            metadata.input_tokens,
            metadata.output_tokens,
        )
        return LLMResult(diagnosis=diagnosis, metadata=metadata)

    def _log_failure(
        self,
        request_id: str | None,
        started_at: float,
        category: str,
    ) -> None:
        logger.warning(
            "LLM request failed request_id=%s model=%s prompt_version=%s latency_ms=%d category=%s",
            request_id,
            self.config.model,
            self.config.prompt_version,
            _latency_ms(self.monotonic(), started_at),
            category,
        )


def _find_output_text(body: dict[str, Any]) -> str:
    for item in body["output"]:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            output_text: object = content.get("text")
            if content.get("type") == "output_text" and isinstance(output_text, str):
                return output_text
    raise ValueError("response has no output text")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _latency_ms(finished_at: float, started_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))
