from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from uuid import uuid4

import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.notification import NotificationErrorCategory, NotificationProviderError
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.providers import create_notification_provider
from dagsentry.providers.smtp import SMTPNotificationProvider, SMTPProviderConfig
from tests.contracts.notification import notification_payload


class FakeSMTP:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[tuple[EmailMessage, str | None, list[str] | None]] = []
        self.starttls_calls = 0
        self.logins: list[tuple[str, str]] = []

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self, **_kwargs: object) -> None:
        self.starttls_calls += 1

    def ehlo(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.logins.append((username, password))

    def send_message(
        self, message: EmailMessage, *, from_addr: str | None, to_addrs: list[str] | None
    ) -> dict[str, tuple[int, bytes]]:
        if self.error is not None:
            raise self.error
        self.messages.append((message, from_addr, to_addrs))
        return {}


def config(
    *,
    username: str | None = None,
    password: str | None = None,
    use_starttls: bool = True,
    use_ssl: bool = False,
    retry_backoff_seconds: float = 0.5,
) -> SMTPProviderConfig:
    return SMTPProviderConfig(
        host="smtp.example.test",
        port=587,
        sender="alerts@example.test",
        recipients=("oncall@example.test",),
        username=username,
        password=password,
        use_starttls=use_starttls,
        use_ssl=use_ssl,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def test_smtp_sends_complete_diagnosis_email() -> None:
    client = FakeSMTP()
    provider = SMTPNotificationProvider(
        config(username="user", password="secret"), client_factory=lambda: client
    )
    payload = notification_payload()

    assert provider.send(payload, delivery_key="delivery-key") == 250

    message, sender, recipients = client.messages[0]
    body = message.get_content()
    assert message["Subject"] == "[DagSentry] 최종 실패 (FAILED): orders.load"
    assert "KST" in body
    assert "권장 조치:" in body
    assert "근거:" in body
    assert message["X-DagSentry-Delivery-Key"] == "delivery-key"
    assert sender == "alerts@example.test"
    assert recipients == ["oncall@example.test"]
    assert client.starttls_calls == 1
    assert client.logins == [("user", "secret")]
    for value in (
        payload.dag_run_id,
        payload.root_cause,
        payload.evidence[0].text,
        payload.airflow_log_url,
    ):
        assert value is not None and value in body


def test_smtp_retries_unavailable_server() -> None:
    clients = [FakeSMTP(error=OSError("unavailable")), FakeSMTP()]
    sleeps: list[float] = []
    provider = SMTPNotificationProvider(
        config(retry_backoff_seconds=0.2),
        client_factory=lambda: clients.pop(0),
        sleep=sleeps.append,
    )

    assert provider.send(notification_payload(), delivery_key="key") == 250
    assert sleeps == [0.2]


def test_smtp_authentication_failure_is_not_retried() -> None:
    provider = SMTPNotificationProvider(
        config(),
        client_factory=lambda: FakeSMTP(error=smtplib.SMTPAuthenticationError(535, b"bad")),
    )

    with pytest.raises(NotificationProviderError) as raised:
        provider.send(notification_payload(), delivery_key="key")

    assert raised.value.category == NotificationErrorCategory.AUTHENTICATION
    assert raised.value.retryable is False


def test_smtp_supports_recovery_email_and_settings_selection() -> None:
    settings = Settings(
        notification_provider="smtp",
        smtp_host="smtp.example.test",
        smtp_from="alerts@example.test",
        smtp_to=["oncall@example.test"],
        smtp_username="user",
        smtp_password=SecretStr("secret"),
    )
    provider = create_notification_provider(settings)
    assert isinstance(provider, SMTPNotificationProvider)
    client = FakeSMTP()
    provider.client_factory = lambda: client
    recovery = RecoveryNotificationPayload(
        incident_id=uuid4(),
        incident_status=IncidentStatus.RECOVERED,
        incident_failure_count=2,
        environment="production",
        dag_id="orders",
        task_id="load",
        recovered_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert isinstance(provider, SMTPNotificationProvider)
    assert provider.send(recovery, delivery_key="recovery-key") == 250
    assert "recovered" in client.messages[0][0]["Subject"].lower()
    assert "secret" not in repr(provider.config)


def test_smtp_settings_require_recipient_and_valid_tls_combination() -> None:
    with pytest.raises(ValueError, match="at least one recipient"):
        SMTPNotificationProvider.from_settings(
            Settings(
                notification_provider="smtp",
                smtp_host="smtp.example.test",
                smtp_from="alerts@example.test",
            )
        )
    with pytest.raises(ValueError, match="cannot both"):
        config(use_starttls=True, use_ssl=True)
