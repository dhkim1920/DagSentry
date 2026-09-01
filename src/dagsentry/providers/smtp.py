"""SMTP email Notification Provider."""

from __future__ import annotations

import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol, Self

from dagsentry.config import Settings
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationPayload,
    NotificationProviderError,
)
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload

SMTPPayload = NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload


class SMTPClient(Protocol):
    """Minimal SMTP client behavior used by the notification provider."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None: ...

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def ehlo(self) -> object: ...

    def login(self, username: str, password: str) -> object: ...

    def send_message(
        self,
        message: EmailMessage,
        *,
        from_addr: str | None,
        to_addrs: list[str] | None,
    ) -> dict[str, tuple[int, bytes]]: ...


@dataclass(frozen=True)
class SMTPProviderConfig:
    """SMTP connection details and bounded retry settings."""

    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    username: str | None = None
    password: str | None = None
    use_starttls: bool = True
    use_ssl: bool = False
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not self.host or not self.sender or not self.recipients:
            raise ValueError("SMTP host, sender, and at least one recipient must be configured")
        if any(not recipient for recipient in self.recipients):
            raise ValueError("SMTP recipients must not be empty")
        if self.port < 1 or self.port > 65_535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if (self.username is None) != (self.password is None):
            raise ValueError("SMTP username and password must be configured together")
        if self.use_starttls and self.use_ssl:
            raise ValueError("SMTP STARTTLS and SSL cannot both be enabled")
        if self.timeout_seconds <= 0 or self.max_attempts < 1 or self.retry_backoff_seconds < 0:
            raise ValueError("SMTP retry and timeout settings are invalid")

    def __repr__(self) -> str:
        return (
            "SMTPProviderConfig("
            f"host={self.host!r}, port={self.port!r}, sender={self.sender!r}, "
            f"recipients={self.recipients!r}, username={self.username!r}, password='**********', "
            f"use_starttls={self.use_starttls!r}, use_ssl={self.use_ssl!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, max_attempts={self.max_attempts!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r})"
        )


class SMTPNotificationProvider:
    """Send plain-text Diagnosis, recovery, and daily-report emails."""

    name = "smtp"

    def __init__(
        self,
        config: SMTPProviderConfig,
        *,
        client_factory: Callable[[], SMTPClient] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.client_factory = client_factory or self._create_client
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> SMTPNotificationProvider:
        """Build the explicitly configured SMTP Provider."""
        if settings.notification_provider != "smtp":
            raise ValueError("SMTP Notification Provider is not enabled")
        if settings.smtp_host is None or settings.smtp_from is None:
            raise ValueError("SMTP host and sender must be configured")
        return cls(
            SMTPProviderConfig(
                host=settings.smtp_host,
                port=settings.smtp_port,
                sender=settings.smtp_from,
                recipients=tuple(settings.smtp_to),
                username=settings.smtp_username,
                password=(
                    settings.smtp_password.get_secret_value()
                    if settings.smtp_password is not None
                    else None
                ),
                use_starttls=settings.smtp_use_starttls,
                use_ssl=settings.smtp_use_ssl,
                timeout_seconds=settings.smtp_timeout_seconds,
                max_attempts=settings.smtp_max_attempts,
                retry_backoff_seconds=settings.smtp_retry_backoff_seconds,
            )
        )

    def send(self, payload: SMTPPayload, *, delivery_key: str) -> int:
        """Send one message; SMTP has no remote idempotency primitive."""
        message = _message(payload, self.config, delivery_key)
        for attempt in range(self.config.max_attempts):
            try:
                with self.client_factory() as client:
                    if self.config.use_starttls:
                        client.starttls(context=ssl.create_default_context())
                        client.ehlo()
                    if self.config.username is not None:
                        assert self.config.password is not None
                        client.login(self.config.username, self.config.password)
                    rejected = client.send_message(
                        message,
                        from_addr=self.config.sender,
                        to_addrs=list(self.config.recipients),
                    )
                    if rejected:
                        raise smtplib.SMTPRecipientsRefused(rejected)
                return 250
            except Exception as error:
                provider_error = _provider_error(error)
                if provider_error.retryable and attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                raise provider_error from None
        raise RuntimeError("unreachable SMTP request state")  # pragma: no cover

    def _create_client(self) -> SMTPClient:
        if self.config.use_ssl:
            return smtplib.SMTP_SSL(
                self.config.host,
                self.config.port,
                timeout=self.config.timeout_seconds,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(self.config.host, self.config.port, timeout=self.config.timeout_seconds)


def _provider_error(error: Exception) -> NotificationProviderError:
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return NotificationProviderError(
            "SMTP authentication failed",
            category=NotificationErrorCategory.AUTHENTICATION,
            retryable=False,
        )
    if isinstance(error, (TimeoutError, smtplib.SMTPServerDisconnected)):
        return NotificationProviderError(
            "SMTP is unavailable", category=NotificationErrorCategory.TIMEOUT, retryable=True
        )
    if isinstance(error, OSError):
        return NotificationProviderError(
            "SMTP is unavailable", category=NotificationErrorCategory.UNAVAILABLE, retryable=True
        )
    return NotificationProviderError(
        "SMTP notification failed",
        category=NotificationErrorCategory.INVALID_REQUEST,
        retryable=False,
    )


def _message(payload: SMTPPayload, config: SMTPProviderConfig, delivery_key: str) -> EmailMessage:
    message = EmailMessage()
    subject, body = _content(payload, delivery_key)
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["X-DagSentry-Delivery-Key"] = delivery_key
    message.set_content(body)
    return message


def _content(payload: SMTPPayload, delivery_key: str) -> tuple[str, str]:
    if isinstance(payload, NotificationPayload):
        evidence = (
            "\n".join(f"- L{item.line_id}: {item.text}" for item in payload.evidence) or "- None"
        )
        actions = "\n".join(f"- {item}" for item in payload.recommended_actions) or "- None"
        return (
            f"[DagSentry] {payload.classification.value}: {payload.dag_id}.{payload.task_id} failed",
            f"""DagSentry failure notification

Environment: {payload.environment}
DAG: {payload.dag_id}
DAG run: {payload.dag_run_id}
Task: {payload.task_id} (map {payload.map_index}, try {payload.try_number})
Failed at: {payload.failed_at.isoformat()}
Classification: {payload.classification.value}
Incident: {payload.incident_id} ({payload.incident_status.value}, {payload.incident_failure_count} failure(s))
Diagnosis: {payload.diagnosis_source.value}, confidence {payload.confidence:.0%}, retry {payload.retry_decision.value}
Root cause: {payload.root_cause or "No root cause available"}
Error signature: {payload.error_signature or "Not available"}
Airflow log: {payload.airflow_log_url or "Not available"}

Evidence:
{evidence}

Recommended actions:
{actions}

Delivery key: {delivery_key}
""",
        )
    if isinstance(payload, RecoveryNotificationPayload):
        return (
            f"[DagSentry] Recovered: {payload.dag_id}.{payload.task_id}",
            f"""DagSentry recovery notification

Environment: {payload.environment}
DAG: {payload.dag_id}
Task: {payload.task_id}
Incident: {payload.incident_id} ({payload.incident_failure_count} linked failure(s))
Recovered at: {payload.recovered_at.isoformat()}
Delivery key: {delivery_key}
""",
        )
    report = payload.rule_based_report
    ai_summary = (
        "\n".join(payload.ai_summary.key_changes + payload.ai_summary.priorities)
        if payload.ai_summary
        else "None"
    )
    return (
        f"[DagSentry] {report.title}",
        f"""{report.title}

{report.overview}

Highlights:
"""
        + "\n".join(f"- {item}" for item in report.highlights)
        + """

Priorities:
"""
        + "\n".join(f"- {item}" for item in report.priorities)
        + f"""

AI summary:
{ai_summary}

Delivery key: {delivery_key}
""",
    )
