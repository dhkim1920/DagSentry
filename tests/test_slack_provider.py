from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.daily_report import build_rule_based_report
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProvider,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.providers import create_notification_provider
from dagsentry.providers.slack import SlackNotificationProvider, SlackProviderConfig
from dagsentry.providers.webhook import WebhookNotificationProvider
from tests.contracts.notification import NotificationProviderContract, notification_payload
from tests.test_daily_report import statistics


class TestSlackNotificationProviderContract(NotificationProviderContract):
    """Run the shared HTTP Notification contract against Slack chat.postMessage."""

    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> NotificationProvider:
        def slack_handler(request: httpx.Request) -> httpx.Response:
            response = handler(request)
            if 200 <= response.status_code < 300 and not response.content:
                return httpx.Response(response.status_code, json={"ok": True})
            return response

        return SlackNotificationProvider(
            SlackProviderConfig(
                bot_token="xoxb-secret",
                channel="C123456",
                max_attempts=2,
                retry_backoff_seconds=self.retry_backoff_seconds,
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(slack_handler)),
            sleep=sleep,
        )

    def assert_success_request(
        self,
        request: httpx.Request,
        payload: NotificationPayload,
        delivery_key: str,
    ) -> None:
        body: dict[str, Any] = json.loads(request.content)
        serialized = json.dumps(body)

        assert request.url == "https://slack.com/api/chat.postMessage"
        assert request.headers["Authorization"] == "Bearer xoxb-secret"
        assert body["channel"] == "C123456"
        assert body["metadata"]["event_payload"]["delivery_key"] == delivery_key
        assert body["metadata"]["event_payload"]["failure_event_id"] == str(
            payload.failure_event_id
        )
        blocks = body["blocks"]
        assert blocks[1]["text"] == {
            "type": "mrkdwn",
            "text": "*orders · load*\n```Invalid order```",
        }
        assert "*Environment:* `production`" in blocks[2]["text"]["text"]
        assert "*Diagnosis:* *AI*" in blocks[2]["text"]["text"]
        assert blocks[3] == {"type": "divider"}
        assert blocks[4]["text"]["text"] == "*Evidence*\n```L7: ValueError: invalid order```"
        assert blocks[5]["text"]["text"] == "*Recommended actions*\n• Validate input"
        assert blocks[6]["text"]["text"] == (
            "*Airflow:* <https://airflow.example/dags/orders/runs/run/tasks/load|Open task log>"
        )
        for required in (
            payload.dag_id,
            payload.dag_run_id,
            payload.task_id,
            payload.classification.value,
            payload.root_cause,
            payload.evidence[0].text,
            payload.recommended_actions[0],
            payload.airflow_log_url,
        ):
            assert required is not None and required in serialized


def slack_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> SlackNotificationProvider:
    return SlackNotificationProvider(
        SlackProviderConfig(
            bot_token="xoxb-secret",
            channel="C123456",
            max_attempts=2,
            retry_backoff_seconds=0.2,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )


@pytest.mark.parametrize(
    ("slack_error", "category", "retryable"),
    [
        ("invalid_auth", NotificationErrorCategory.AUTHENTICATION, False),
        ("missing_scope", NotificationErrorCategory.AUTHORIZATION, False),
        ("invalid_blocks", NotificationErrorCategory.INVALID_REQUEST, False),
        ("internal_error", NotificationErrorCategory.UNAVAILABLE, True),
    ],
)
def test_slack_maps_http_200_api_errors_without_leaking_body(
    slack_error: str,
    category: NotificationErrorCategory,
    retryable: bool,
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": slack_error, "secret": "hidden"})

    with pytest.raises(NotificationProviderError) as raised:
        slack_provider(handle).send(
            notification_payload(),
            delivery_key="key",
        )

    assert raised.value.category == category
    assert raised.value.retryable is retryable
    assert "hidden" not in str(raised.value)


def test_slack_honors_bounded_retry_after_header() -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    assert (
        slack_provider(handle, sleep=sleeps.append).send(notification_payload(), delivery_key="key")
        == 200
    )
    assert sleeps == [3.0]


def test_slack_supports_recovery_and_daily_report_payloads() -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    provider = slack_provider(handle)
    recovery = RecoveryNotificationPayload(
        incident_id=uuid4(),
        incident_status=IncidentStatus.RECOVERED,
        incident_failure_count=2,
        environment="production",
        dag_id="orders",
        task_id="load",
        recovered_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    stats = statistics()
    report = DailyReportNotificationPayload(
        statistics=stats,
        rule_based_report=build_rule_based_report(stats),
    )

    provider.send(recovery, delivery_key="recovery-key")
    provider.send(report, delivery_key="report-key")

    assert [body["metadata"]["event_type"] for body in bodies] == [
        "dagsentry_incident_recovered",
        "dagsentry_daily_report_created",
    ]
    assert bodies[1]["metadata"]["event_payload"]["delivery_key"] == "report-key"


def test_slack_settings_factory_and_masked_config() -> None:
    settings = Settings(
        notification_provider="slack",
        slack_bot_token=SecretStr("xoxb-do-not-log"),
        slack_channel="C123456",
    )

    provider = create_notification_provider(settings)

    assert isinstance(provider, SlackNotificationProvider)
    assert "xoxb-do-not-log" not in repr(provider.config)
    assert "**********" in repr(provider.config)


def test_slack_settings_require_token_and_channel() -> None:
    with pytest.raises(ValueError, match="bot token and channel"):
        SlackNotificationProvider.from_settings(Settings(notification_provider="slack"))


def test_notification_factory_keeps_webhook_as_default() -> None:
    provider = create_notification_provider(Settings(webhook_url="https://webhook.test/dagsentry"))

    assert isinstance(provider, WebhookNotificationProvider)


def test_slack_requires_https_for_public_endpoint() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        SlackProviderConfig(
            bot_token="xoxb-secret",
            channel="C123456",
            base_url="http://slack.example/api",
        )
