from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from dagsentry.config import Settings
from dagsentry.daily_report import build_rule_based_report
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.notification import NotificationPayload, NotificationProvider
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload
from dagsentry.providers import create_notification_provider
from dagsentry.providers.discord import DiscordNotificationProvider, DiscordProviderConfig
from tests.contracts.notification import NotificationProviderContract, notification_payload
from tests.test_daily_report import statistics


class TestDiscordNotificationProviderContract(NotificationProviderContract):
    """Run the shared HTTP Notification contract against Discord Incoming Webhooks."""

    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> NotificationProvider:
        return DiscordNotificationProvider(
            DiscordProviderConfig(
                webhook_url="https://discord.test/api/webhooks/123/secret-token",
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
        body: dict[str, Any] = json.loads(request.content)
        serialized = json.dumps(body)

        assert request.url.params["wait"] == "true"
        assert "Authorization" not in request.headers
        assert body["allowed_mentions"] == {"parse": []}
        assert delivery_key in body["embeds"][0]["footer"]["text"]
        for required in (
            payload.dag_id,
            payload.dag_run_id,
            payload.task_id,
            payload.classification.value,
            payload.root_cause,
            payload.evidence[0].text,
            payload.recommended_actions[0],
            payload.airflow_log_url,
            str(payload.failure_event_id),
            str(payload.diagnosis_id),
        ):
            assert required is not None and required in serialized


def discord_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> DiscordNotificationProvider:
    return DiscordNotificationProvider(
        DiscordProviderConfig(
            webhook_url="https://discord.test/api/webhooks/123/secret-token",
            max_attempts=2,
            retry_backoff_seconds=0.2,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )


@pytest.mark.parametrize(
    ("headers", "body", "expected_delay"),
    [
        ({"Retry-After": "3"}, {"retry_after": 9}, 3.0),
        ({}, {"retry_after": 4.5}, 4.5),
        ({"Retry-After": "120"}, {}, 60.0),
    ],
)
def test_discord_honors_bounded_rate_limit_delay(
    headers: dict[str, str],
    body: dict[str, object],
    expected_delay: float,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers=headers, json=body)
        return httpx.Response(200, json={"id": "message-id"})

    assert (
        discord_provider(handle, sleep=sleeps.append).send(
            notification_payload(), delivery_key="key"
        )
        == 200
    )
    assert sleeps == [expected_delay]


def test_discord_supports_recovery_and_daily_report_payloads() -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "message-id"})

    provider = discord_provider(handle)
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

    assert bodies[0]["embeds"][0]["title"] == "Incident recovered"
    assert bodies[1]["embeds"][0]["title"] == report.rule_based_report.title
    assert "report-key" in bodies[1]["embeds"][0]["footer"]["text"]
    assert all(body["allowed_mentions"] == {"parse": []} for body in bodies)


def test_discord_settings_factory_and_masked_config() -> None:
    secret_url = "https://discord.com/api/webhooks/123/do-not-log"
    settings = Settings(
        notification_provider="discord",
        discord_webhook_url=secret_url,
    )

    provider = create_notification_provider(settings)

    assert isinstance(provider, DiscordNotificationProvider)
    assert secret_url not in repr(provider.config)
    assert "do-not-log" not in repr(provider.config)
    assert "**********" in repr(provider.config)


def test_discord_settings_require_webhook_url() -> None:
    with pytest.raises(ValueError, match="Webhook URL"):
        DiscordNotificationProvider.from_settings(Settings(notification_provider="discord"))
