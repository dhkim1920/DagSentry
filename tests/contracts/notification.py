"""Use the shipped public contract harness for built-in adapters too."""

from dagsentry.extensions.testing.notification import (
    NotificationProviderContract,
    notification_payload,
)

__all__ = ["NotificationProviderContract", "notification_payload"]
