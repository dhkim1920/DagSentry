"""Generic JSON Webhook Notification Provider."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from dagsentry.config import Settings
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload


@dataclass(frozen=True)
class WebhookProviderConfig:
    """Connection and bounded retry settings for one JSON Webhook."""

    url: str
    bearer_token: str | None = None
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Webhook URL must be an HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

    def __repr__(self) -> str:
        token = "'**********'" if self.bearer_token is not None else "None"
        return (
            f"WebhookProviderConfig(url={self.url!r}, bearer_token={token}, "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class WebhookNotificationProvider:
    """Deliver the neutral payload to a configurable JSON endpoint."""

    name = "webhook"

    def __init__(
        self,
        config: WebhookProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=config.timeout_seconds,
        )
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> WebhookNotificationProvider:
        """Build the configured v0.1 Notification Provider."""
        if settings.webhook_url is None:
            raise ValueError("Webhook URL is not configured")
        return cls(
            WebhookProviderConfig(
                url=settings.webhook_url,
                bearer_token=(
                    settings.webhook_bearer_token.get_secret_value()
                    if settings.webhook_bearer_token is not None
                    else None
                ),
                timeout_seconds=settings.webhook_timeout_seconds,
                max_attempts=settings.webhook_max_attempts,
                retry_backoff_seconds=settings.webhook_retry_backoff_seconds,
            )
        )

    def send(
        self,
        payload: NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload,
        *,
        delivery_key: str,
    ) -> int:
        """POST one idempotency-keyed JSON payload with bounded transient retries."""
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": delivery_key,
        }
        if self.config.bearer_token is not None:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"

        for attempt in range(self.config.max_attempts):
            try:
                response = self.http_client.post(
                    self.config.url,
                    headers=headers,
                    json=payload.model_dump(
                        mode="json",
                        exclude={"failure_state"}
                        if isinstance(payload, NotificationPayload)
                        else None,
                    ),
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                raise NotificationProviderError(
                    "Webhook request timed out",
                    category=NotificationErrorCategory.TIMEOUT,
                    retryable=True,
                ) from None
            except httpx.TransportError:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                raise NotificationProviderError(
                    "Webhook is unavailable",
                    category=NotificationErrorCategory.UNAVAILABLE,
                    retryable=True,
                ) from None

            error = _http_error(response.status_code)
            if error is None:
                return response.status_code
            if error.retryable and attempt + 1 < self.config.max_attempts:
                self.sleep(self.config.retry_backoff_seconds)
                continue
            raise error

        raise RuntimeError("unreachable Webhook request state")  # pragma: no cover


def _http_error(status_code: int) -> NotificationProviderError | None:
    if 200 <= status_code < 300:
        return None
    if status_code == 401:
        return NotificationProviderError(
            "Webhook authentication failed",
            category=NotificationErrorCategory.AUTHENTICATION,
            retryable=False,
            response_status=status_code,
        )
    if status_code == 403:
        return NotificationProviderError(
            "Webhook authorization failed",
            category=NotificationErrorCategory.AUTHORIZATION,
            retryable=False,
            response_status=status_code,
        )
    if status_code == 429:
        return NotificationProviderError(
            "Webhook rate limited the request",
            category=NotificationErrorCategory.RATE_LIMITED,
            retryable=True,
            response_status=status_code,
        )
    if status_code in {408} or status_code >= 500:
        return NotificationProviderError(
            "Webhook is unavailable",
            category=NotificationErrorCategory.UNAVAILABLE,
            retryable=True,
            response_status=status_code,
        )
    return NotificationProviderError(
        "Webhook rejected the request",
        category=NotificationErrorCategory.INVALID_REQUEST,
        retryable=False,
        response_status=status_code,
    )
