from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select

from dagsentry.config import Settings
from dagsentry.daily_report import DailyReportService
from dagsentry.db import SessionFactory
from dagsentry.domain.notification import NotificationErrorCategory, NotificationProviderError
from dagsentry.models import NotificationDeliveryRecord
from dagsentry.notification import deliver_notification
from dagsentry.providers import create_notification_provider
from dagsentry.providers.fallback import FallbackNotificationProvider
from dagsentry.providers.notification_adapter import NotificationMessage, provider_names_overlap
from dagsentry.recovery import RecoveryChecker
from tests.test_daily_report import REPORT_DATE
from tests.test_notification import notification_payload, stored_diagnosis
from tests.test_recovery import NOW, StubTaskStateClient, create_incident


@dataclass
class Provider:
    name: str
    error: NotificationProviderError | None = None
    keys: list[str] = field(default_factory=list)

    def send(self, payload: NotificationMessage, *, delivery_key: str) -> int:
        self.keys.append(delivery_key)
        if self.error is not None:
            raise self.error
        return 204


def failure(*, retryable: bool = True) -> NotificationProviderError:
    return NotificationProviderError(
        "disposable-private-message",
        category=NotificationErrorCategory.UNAVAILABLE,
        retryable=retryable,
    )


@pytest.mark.parametrize("primary_fails", [False, True])
def test_fallback_delivers_once_and_keeps_one_record(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
    primary_fails: bool,
) -> None:
    payload = notification_payload(*stored_diagnosis(session_factory))
    primary = Provider("teams", failure() if primary_fails else None)
    secondary = Provider("smtp")
    chain = FallbackNotificationProvider((primary, secondary))
    first = deliver_notification(session_factory, provider=chain, payload=payload)
    second = deliver_notification(session_factory, provider=chain, payload=payload)
    assert first.delivered and second.already_delivered
    assert primary.keys == [first.delivery_key]
    assert secondary.keys == ([first.delivery_key] if primary_fails else [])
    assert "disposable-private-message" not in caplog.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1
        record = session.get(NotificationDeliveryRecord, first.delivery_id)
        assert record is not None
        assert record.provider == "teams>smtp"
        assert record.attempt_count == 1


def test_all_failures_raise_primary_error_and_preserve_key(session_factory: SessionFactory) -> None:
    payload = notification_payload(*stored_diagnosis(session_factory))
    primary_error = failure(retryable=False)
    primary = Provider("teams", primary_error)
    secondary = Provider("smtp", failure(retryable=True))
    chain = FallbackNotificationProvider((primary, secondary))
    with pytest.raises(NotificationProviderError) as raised:
        deliver_notification(session_factory, provider=chain, payload=payload)
    assert raised.value is primary_error
    assert not raised.value.retryable
    secondary.error = None
    result = deliver_notification(session_factory, provider=chain, payload=payload)
    assert primary.keys == secondary.keys == [result.delivery_key, result.delivery_key]


def test_report_failed_before_fallback_was_added_can_resume(
    session_factory: SessionFactory,
) -> None:
    primary = Provider("teams", failure())
    service = DailyReportService(session_factory, notification_provider=primary)
    with pytest.raises(NotificationProviderError):
        service.run(REPORT_DATE, "test")
    secondary = Provider("smtp")
    service = DailyReportService(
        session_factory,
        notification_provider=FallbackNotificationProvider((primary, secondary)),
    )
    service.run(REPORT_DATE, "test")
    service.run(REPORT_DATE, "test")
    assert primary.keys == [secondary.keys[0], secondary.keys[0]]
    assert len(secondary.keys) == 1


@pytest.mark.parametrize(
    ("stored", "current", "expected"),
    [
        ("teams", "teams>smtp", True),
        ("teams>smtp", "smtp", True),
        ("slack", "teams>smtp", False),
    ],
)
def test_provider_chain_membership(stored: str, current: str, expected: bool) -> None:
    assert provider_names_overlap(stored, current) is expected


def test_environment_factory_builds_configured_chain() -> None:
    provider = create_notification_provider(
        Settings(
            notification_provider="teams",
            teams_webhook_url="https://example.test/webhook",
            notification_fallback_providers=["smtp"],
            smtp_host="smtp.example.test",
            smtp_from="sender@example.test",
            smtp_to=["recipient@example.test"],
        )
    )
    assert isinstance(provider, FallbackNotificationProvider)
    assert provider.name == "teams>smtp"


def test_recovery_resumes_existing_primary_record_with_fallback(
    session_factory: SessionFactory,
) -> None:
    _, references = create_incident(session_factory, task_count=1)
    primary = Provider("teams", failure())
    airflow = StubTaskStateClient({references[0]: "SUCCESS"})
    checker = RecoveryChecker(
        session_factory, airflow_client=airflow, notification_provider=primary, clock=lambda: NOW
    )
    assert checker.run_once().notification_failures == 1
    secondary = Provider("smtp")
    checker = RecoveryChecker(
        session_factory,
        airflow_client=airflow,
        notification_provider=FallbackNotificationProvider((primary, secondary)),
        clock=lambda: NOW,
    )
    assert checker.run_once().notifications_delivered == 1
    assert checker.run_once().notifications_delivered == 0
    assert primary.keys == [secondary.keys[0], secondary.keys[0]]
    assert len(secondary.keys) == 1
