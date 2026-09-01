from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from dagsentry.domain.notification import NotificationPayload, NotificationProvider
from dagsentry.providers.webhook import WebhookNotificationProvider, WebhookProviderConfig
from tests.contracts.notification import NotificationProviderContract


class TestWebhookNotificationProviderContract(NotificationProviderContract):
    """Run the shared HTTP Notification contract against the Webhook adapter."""

    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> NotificationProvider:
        return WebhookNotificationProvider(
            WebhookProviderConfig(
                url="https://webhook.test/diagnoses",
                bearer_token="secret-token",
                max_attempts=2,
                retry_backoff_seconds=self.retry_backoff_seconds,
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=sleep,
        )

    def assert_success_request(
        self,
        request: httpx.Request,
        payload: NotificationPayload,
        delivery_key: str,
    ) -> None:
        assert request.headers["Idempotency-Key"] == delivery_key
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert json.loads(request.content) == payload.model_dump(mode="json")


def test_webhook_config_repr_masks_bearer_token() -> None:
    config = WebhookProviderConfig(
        url="https://webhook.test",
        bearer_token="do-not-log",
    )

    assert "do-not-log" not in repr(config)
    assert "**********" in repr(config)
