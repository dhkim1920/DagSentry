"""Sequential notification fallback with primary error semantics."""

import logging

from dagsentry.domain.notification import NotificationProviderError
from dagsentry.providers.notification_adapter import (
    NotificationMessage,
    NotificationProviderAdapter,
)

logger = logging.getLogger(__name__)


class FallbackNotificationProvider:
    def __init__(self, providers: tuple[NotificationProviderAdapter, ...]) -> None:
        if not providers:
            raise ValueError("at least one notification provider is required")
        self.providers = providers
        self.name = ">".join(provider.name for provider in providers)

    def send(self, payload: NotificationMessage, *, delivery_key: str) -> int:
        primary_error: NotificationProviderError | None = None
        for provider in self.providers:
            try:
                return provider.send(payload, delivery_key=delivery_key)
            except NotificationProviderError as error:
                if primary_error is None:
                    primary_error = error
                logger.warning(
                    "notification provider failed provider=%s category=%s",
                    provider.name,
                    error.category.value,
                )
        assert primary_error is not None
        raise primary_error
