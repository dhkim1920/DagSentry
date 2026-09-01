"""Ollama Chat API adapter for structured AI Diagnosis."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaProviderConfig:
    """Ollama endpoint, model, output, and bounded retry settings."""

    model: str
    prompt_version: str
    base_url: str = "http://localhost:11434/api"
    max_output_tokens: int = 2_048
    timeout_seconds: float = 15.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama API base URL must be an HTTP(S) URL")
        if not self.model or not self.prompt_version:
            raise ValueError("Ollama model and prompt version are required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")


class OllamaProvider:
    """Call the Ollama Chat API without exposing its types to Core."""

    def __init__(
        self,
        config: OllamaProviderConfig,
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
    def from_settings(cls, settings: Settings) -> OllamaProvider:
        """Build the explicitly configured Ollama Provider."""
        if settings.llm_provider != "ollama":
            raise ValueError("Ollama LLM Provider is not enabled")
        if settings.llm_model is None:
            raise ValueError("Ollama model must be configured")
        return cls(
            OllamaProviderConfig(
                model=settings.llm_model,
                prompt_version=settings.llm_prompt_version,
                base_url=settings.ollama_api_base_url,
                max_output_tokens=settings.ollama_max_output_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
                max_attempts=settings.llm_max_attempts,
                retry_backoff_seconds=settings.llm_retry_backoff_seconds,
            )
        )

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        """Request and parse one strict structured Diagnosis."""
        started_at = self.monotonic()
        for attempt in range(self.config.max_attempts):
            try:
                response = self.http_client.post(
                    f"{self.config.base_url.rstrip('/')}/chat",
                    headers={"Content-Type": "application/json"},
                    json=self._request_body(request),
                    timeout=self.config.timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(started_at, "transport_error")
                raise LLMProviderError("Ollama request failed after retry") from None

            if response.status_code in {408, 429} or response.status_code >= 500:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(started_at, f"http_{response.status_code}")
                raise LLMProviderError("Ollama request failed after retry")
            if response.status_code >= 400:
                self._log_failure(started_at, f"http_{response.status_code}")
                raise LLMProviderError(f"Ollama request failed with HTTP {response.status_code}")
            return self._parse_response(response, started_at)

        raise RuntimeError("unreachable Ollama request state")  # pragma: no cover

    def _request_body(self, request: AIDiagnosisRequest) -> dict[str, object]:
        return {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Diagnose the Airflow Task failure using only the supplied JSON context. "
                        "Every evidence item must copy one supplied excerpt line_id and text "
                        "exactly."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        request.to_json_value(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "format": AIDiagnosisResponse.model_json_schema(),
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": self.config.max_output_tokens,
            },
        }

    def _parse_response(self, response: httpx.Response, started_at: float) -> LLMResult:
        try:
            body: dict[str, Any] = response.json()
            if body.get("done") is not True:
                raise ValueError("Ollama response is incomplete")
            diagnosis = AIDiagnosisResponse.model_validate_json(_message_content(body))
            metadata = LLMCallMetadata(
                request_id=None,
                model=_optional_string(body.get("model")) or self.config.model,
                prompt_version=self.config.prompt_version,
                latency_ms=_latency_ms(self.monotonic(), started_at),
                input_tokens=_optional_int(body.get("prompt_eval_count")),
                output_tokens=_optional_int(body.get("eval_count")),
            )
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ):
            self._log_failure(started_at, "invalid_response")
            raise LLMProviderError("Ollama returned an invalid structured response") from None

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

    def _log_failure(self, started_at: float, category: str) -> None:
        logger.warning(
            "LLM request failed request_id=%s model=%s prompt_version=%s latency_ms=%d category=%s",
            None,
            self.config.model,
            self.config.prompt_version,
            _latency_ms(self.monotonic(), started_at),
            category,
        )


def _message_content(body: dict[str, Any]) -> str:
    message = body["message"]
    if not isinstance(message, dict):
        raise ValueError("response message is not an object")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("response message has no content")
    return content


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _latency_ms(finished_at: float, started_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))
