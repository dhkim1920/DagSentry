"""Shared runtime boundary for all notification payloads."""

from typing import Protocol

from dagsentry.domain.notification import NotificationPayload
from dagsentry.domain.recovery import RecoveryNotificationPayload
from dagsentry.domain.reporting import DailyReportNotificationPayload

NotificationMessage = (
    NotificationPayload | RecoveryNotificationPayload | DailyReportNotificationPayload
)


class NotificationProviderAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, payload: NotificationMessage, *, delivery_key: str) -> int: ...


def provider_names_overlap(stored: str, current: str) -> bool:
    """Allow an existing delivery to resume when its provider gains a fallback."""
    return bool(set(stored.split(">")) & set(current.split(">")))
