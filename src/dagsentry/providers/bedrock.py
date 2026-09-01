"""AWS Bedrock Converse adapter for structured AI Diagnosis."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)
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

_TRANSIENT_AWS_ERRORS = {
    "InternalServerException",
    "ModelErrorException",
    "ModelNotReadyException",
    "ModelStreamErrorException",
    "ModelTimeoutException",
    "ServiceQuotaExceededException",
    "ServiceUnavailableException",
    "ThrottlingException",
}


class BedrockRuntimeClient(Protocol):
    """Minimal boto3 Bedrock Runtime surface consumed by the adapter."""

    def converse(self, **kwargs: object) -> dict[str, Any]:
        """Invoke one model through the normalized Converse API."""


@dataclass(frozen=True)
class BedrockProviderConfig:
    """AWS region, model ID, output, and bounded retry settings."""

    region: str
    model_id: str
    prompt_version: str
    max_output_tokens: int = 2_048
    timeout_seconds: float = 15.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not self.region or not self.model_id or not self.prompt_version:
            raise ValueError("Bedrock region, model ID, and prompt version are required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")


class BedrockProvider:
    """Call AWS Bedrock Converse without exposing AWS types to Core."""

    def __init__(
        self,
        config: BedrockProviderConfig,
        client: BedrockRuntimeClient | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.client = client or _create_client(config)
        self.sleep = sleep
        self.monotonic = monotonic

    @classmethod
    def from_settings(cls, settings: Settings) -> BedrockProvider:
        """Build the explicitly configured Bedrock Provider."""
        if settings.llm_provider != "bedrock":
            raise ValueError("AWS Bedrock LLM Provider is not enabled")
        if settings.bedrock_region is None or settings.llm_model is None:
            raise ValueError("AWS Bedrock region and model ID must be configured")
        return cls(
            BedrockProviderConfig(
                region=settings.bedrock_region,
                model_id=settings.llm_model,
                prompt_version=settings.llm_prompt_version,
                max_output_tokens=settings.bedrock_max_output_tokens,
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
                response = self.client.converse(**self._request_parameters(request))
            except (NoCredentialsError, PartialCredentialsError):
                self._log_failure(None, started_at, "credentials")
                raise LLMProviderError("AWS Bedrock credentials are unavailable") from None
            except ClientError as error:
                request_id = _aws_request_id(error.response)
                code = _aws_error_code(error.response)
                if code in _TRANSIENT_AWS_ERRORS and attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(request_id, started_at, code or "client_error")
                message = (
                    "AWS Bedrock request failed after retry"
                    if code in _TRANSIENT_AWS_ERRORS
                    else "AWS Bedrock request was rejected"
                )
                raise LLMProviderError(message) from None
            except BotoCoreError:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                self._log_failure(None, started_at, "transport_error")
                raise LLMProviderError("AWS Bedrock request failed after retry") from None
            return self._parse_response(response, started_at)

        raise RuntimeError("unreachable Bedrock request state")  # pragma: no cover

    def _request_parameters(self, request: AIDiagnosisRequest) -> dict[str, object]:
        return {
            "modelId": self.config.model_id,
            "system": [
                {
                    "text": (
                        "Diagnose the Airflow Task failure using only the supplied JSON context. "
                        "Every evidence item must copy one supplied excerpt line_id and text exactly."
                    )
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": json.dumps(
                                request.to_json_value(),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        }
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": self.config.max_output_tokens},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(
                                _bedrock_diagnosis_schema(),
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            "name": "dagsentry_ai_diagnosis",
                            "description": "A structured DagSentry Airflow failure diagnosis",
                        }
                    },
                }
            },
        }

    def _parse_response(self, response: dict[str, Any], started_at: float) -> LLMResult:
        request_id = _aws_request_id(response)
        try:
            stop_reason = response.get("stopReason")
            if stop_reason not in {"end_turn", "stop_sequence"}:
                raise ValueError("Bedrock response did not complete structured output")
            diagnosis = AIDiagnosisResponse.model_validate_json(_find_text(response))
            usage = response.get("usage") or {}
            metadata = LLMCallMetadata(
                request_id=request_id,
                model=self.config.model_id,
                prompt_version=self.config.prompt_version,
                latency_ms=_latency_ms(self.monotonic(), started_at),
                input_tokens=_optional_int(usage.get("inputTokens")),
                output_tokens=_optional_int(usage.get("outputTokens")),
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
            raise LLMProviderError("AWS Bedrock returned an invalid structured response") from None

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
            self.config.model_id,
            self.config.prompt_version,
            _latency_ms(self.monotonic(), started_at),
            category,
        )


def _create_client(config: BedrockProviderConfig) -> BedrockRuntimeClient:
    return cast(
        BedrockRuntimeClient,
        boto3.client(
            "bedrock-runtime",
            region_name=config.region,
            config=Config(
                connect_timeout=config.timeout_seconds,
                read_timeout=config.timeout_seconds,
                retries={"total_max_attempts": 1},
            ),
        ),
    )


def _bedrock_diagnosis_schema() -> dict[str, object]:
    """Remove constraints Bedrock grammar compilation does not support."""
    unsupported = {
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "multipleOf",
    }

    def clean(value: object) -> object:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in unsupported}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    schema = clean(AIDiagnosisResponse.model_json_schema())
    assert isinstance(schema, dict)
    return schema


def _find_text(response: dict[str, Any]) -> str:
    content = response["output"]["message"]["content"]
    for item in content:
        text: object = item.get("text")
        if isinstance(text, str):
            return text
    raise ValueError("response has no text content")


def _aws_error_code(response: dict[str, Any]) -> str | None:
    error = response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def _aws_request_id(response: dict[str, Any]) -> str | None:
    metadata = response.get("ResponseMetadata")
    request_id = metadata.get("RequestId") if isinstance(metadata, dict) else None
    return request_id if isinstance(request_id, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _latency_ms(finished_at: float, started_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))
