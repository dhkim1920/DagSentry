"""Example external adapters for a company JSON gateway, using only Provider API v1."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from dagsentry.extensions import (
    AI_DIAGNOSIS_INSTRUCTIONS,
    PROVIDER_API_VERSION,
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    LLMCallMetadata,
    LLMProviderError,
    LLMResult,
    NotificationErrorCategory,
    NotificationMessage,
    NotificationProviderError,
)

assert PROVIDER_API_VERSION == 1


class _Gateway:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        client: httpx.Client,
        attempts: int = 2,
        backoff: float = 0.25,
        timeout: float = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Gateway endpoint must be a credential-free HTTPS URL")
        if not token or attempts < 1 or timeout <= 0 or backoff < 0:
            raise ValueError("Gateway credentials and retry limits are required")
        self.endpoint, self.token, self.client = endpoint, token, client
        self.attempts, self.backoff, self.timeout, self.sleep = attempts, backoff, timeout, sleep

    def post(self, body: dict[str, object], headers: dict[str, str]) -> httpx.Response:
        for attempt in range(self.attempts):
            response = None
            try:
                response = self.client.post(
                    self.endpoint,
                    json=body,
                    headers={"Authorization": f"Bearer {self.token}", **headers},
                    timeout=self.timeout,
                    follow_redirects=False,
                )
                if 200 <= response.status_code < 300:
                    return response
                code = response.status_code
                retryable = code in {408, 429} or code >= 500
                category = {
                    401: NotificationErrorCategory.AUTHENTICATION,
                    403: NotificationErrorCategory.AUTHORIZATION,
                    408: NotificationErrorCategory.TIMEOUT,
                    429: NotificationErrorCategory.RATE_LIMITED,
                }.get(
                    code,
                    NotificationErrorCategory.UNAVAILABLE
                    if code >= 500
                    else NotificationErrorCategory.INVALID_REQUEST,
                )
            except httpx.TransportError as error:
                retryable = True
                category = (
                    NotificationErrorCategory.TIMEOUT
                    if isinstance(error, httpx.TimeoutException)
                    else NotificationErrorCategory.UNAVAILABLE
                )
            if retryable and attempt + 1 < self.attempts:
                self.sleep(self.backoff)
                continue
            raise NotificationProviderError(
                "Gateway request failed",
                category=category,
                retryable=retryable,
                response_status=response.status_code if response is not None else None,
            ) from None
        raise AssertionError("unreachable")


class GatewayLLMProvider:
    def __init__(
        self,
        gateway: _Gateway,
        *,
        model: str,
        prompt_version: str,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gateway, self.model, self.prompt_version = gateway, model, prompt_version
        self.monotonic = monotonic

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        started = self.monotonic()
        try:
            response = self.gateway.post(
                {
                    "model": self.model,
                    "instructions": AI_DIAGNOSIS_INSTRUCTIONS,
                    "context": request.to_json_value(),
                    "schema": AIDiagnosisResponse.model_json_schema(),
                },
                {},
            )
            diagnosis = AIDiagnosisResponse.model_validate_json(response.content)
        except (NotificationProviderError, ValidationError):
            raise LLMProviderError("Gateway diagnosis failed") from None
        return LLMResult(
            diagnosis,
            LLMCallMetadata(
                request_id=response.headers.get("x-request-id"),
                model=self.model,
                prompt_version=self.prompt_version,
                latency_ms=round((self.monotonic() - started) * 1000),
                input_tokens=None,
                output_tokens=None,
            ),
        )


class GatewayNotificationProvider:
    name = "company-gateway"

    def __init__(self, gateway: _Gateway) -> None:
        self.gateway = gateway

    def send(self, payload: NotificationMessage, *, delivery_key: str) -> int:
        return self.gateway.post(
            payload.model_dump(mode="json"), {"Idempotency-Key": delivery_key}
        ).status_code
