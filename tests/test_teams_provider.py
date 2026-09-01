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
from dagsentry.providers.teams import TeamsNotificationProvider, TeamsProviderConfig
from tests.contracts.notification import NotificationProviderContract, notification_payload
from tests.test_daily_report import statistics


class TestTeamsNotificationProviderContract(NotificationProviderContract):
    """Run the shared HTTP Notification contract against Teams Workflows."""

    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> NotificationProvider:
        return TeamsNotificationProvider(
            TeamsProviderConfig(
                webhook_url="https://teams.test/workflows/trigger?sig=secret",
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
        attachment = body["attachments"][0]
        card = attachment["content"]
        serialized = json.dumps(card)

        assert request.method == "POST"
        assert str(request.url) == "https://teams.test/workflows/trigger?sig=secret"
        assert "Authorization" not in request.headers
        assert body["type"] == "message"
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.2"
        assert "actions" not in card
        assert "msteams" not in serialized
        assert delivery_key in serialized
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


def teams_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> TeamsNotificationProvider:
    return TeamsNotificationProvider(
        TeamsProviderConfig(
            webhook_url="https://teams.test/workflows/trigger?sig=secret",
            max_attempts=2,
            retry_backoff_seconds=0.2,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )


def test_teams_honors_bounded_retry_after_delay() -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "120"})
        return httpx.Response(202)

    assert (
        teams_provider(handle, sleep=sleeps.append).send(notification_payload(), delivery_key="key")
        == 202
    )
    assert sleeps == [60.0]


def test_teams_supports_recovery_and_daily_report_payloads() -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202)

    provider = teams_provider(handle)
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

    recovery_card = bodies[0]["attachments"][0]["content"]
    report_card = bodies[1]["attachments"][0]["content"]
    assert "Incident recovered" in json.dumps(recovery_card, ensure_ascii=False)
    assert report.rule_based_report.title in json.dumps(report_card, ensure_ascii=False)
    assert "report-key" in json.dumps(report_card, ensure_ascii=False)


def test_teams_settings_factory_and_masked_config() -> None:
    secret_url = "https://teams.test/workflows/trigger?sig=do-not-log"
    settings = Settings(
        notification_provider="teams",
        teams_webhook_url=secret_url,
    )

    provider = create_notification_provider(settings)

    assert isinstance(provider, TeamsNotificationProvider)
    assert secret_url not in repr(provider.config)
    assert "do-not-log" not in repr(provider.config)
    assert "**********" in repr(provider.config)


def test_teams_settings_require_webhook_url() -> None:
    with pytest.raises(ValueError, match="Webhook URL"):
        TeamsNotificationProvider.from_settings(Settings(notification_provider="teams"))
