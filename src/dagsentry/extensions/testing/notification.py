"""Reusable contract tests for HTTP Notification Providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from dagsentry.domain.diagnosis import DiagnosisSource, ErrorClassification, RetryDecision
from dagsentry.domain.incident import IncidentStatus
from dagsentry.domain.notification import (
    NotificationErrorCategory,
    NotificationEvidence,
    NotificationPayload,
    NotificationProvider,
    NotificationProviderError,
)


def notification_payload() -> NotificationPayload:
    """Return a complete neutral Diagnosis notification fixture."""
    return NotificationPayload(
        failure_event_id=uuid4(),
        diagnosis_id=uuid4(),
        incident_id=uuid4(),
        incident_status=IncidentStatus.OPEN,
        incident_failure_count=1,
        environment="production",
        dag_id="orders",
        dag_run_id="scheduled__2026-08-10",
        task_id="load",
        map_index=-1,
        try_number=1,
        failed_at=datetime(2026, 8, 10, tzinfo=UTC),
        classification=ErrorClassification.DAG_CODE,
        root_cause="Invalid order",
        confidence=0.9,
        evidence=[NotificationEvidence(line_id=7, text="ValueError: invalid order")],
        error_signature="a" * 64,
        recommended_actions=["Validate input"],
        retry_decision=RetryDecision.NOT_RETRYABLE,
        airflow_log_url="https://airflow.example/dags/orders/runs/run/tasks/load",
        diagnosis_source=DiagnosisSource.AI,
        is_rule_fallback=False,
    )


class NotificationProviderContract(ABC):
    """Behavior every bounded HTTP Notification Provider must pass."""

    retry_backoff_seconds = 0.2

    @abstractmethod
    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> NotificationProvider:
        """Build the concrete Provider with two attempts and the contract backoff."""

    @abstractmethod
    def assert_success_request(
        self,
        request: httpx.Request,
        payload: NotificationPayload,
        delivery_key: str,
    ) -> None:
        """Verify the vendor representation retained all required neutral content."""

    def test_contract_sends_complete_payload_with_stable_identity(self) -> None:
        captured: httpx.Request | None = None
        expected_payload = notification_payload()

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal captured
            captured = request
            return httpx.Response(204)

        provider = self.make_provider(handle, sleep=lambda _: None)
        status_code = provider.send(expected_payload, delivery_key="delivery-key")

        assert provider.name
        assert status_code == 204
        assert captured is not None
        self.assert_success_request(captured, expected_payload, "delivery-key")

    @pytest.mark.parametrize("first_status", [408, 429, 503])
    def test_contract_retries_transient_http_failures(self, first_status: int) -> None:
        calls = 0
        sleeps: list[float] = []

        def handle(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(first_status if calls == 1 else 200)

        provider = self.make_provider(handle, sleep=sleeps.append)

        assert provider.send(notification_payload(), delivery_key="key") == 200
        assert calls == 2
        assert sleeps == [self.retry_backoff_seconds]

    @pytest.mark.parametrize(
        ("status_code", "category"),
        [
            (401, NotificationErrorCategory.AUTHENTICATION),
            (403, NotificationErrorCategory.AUTHORIZATION),
            (422, NotificationErrorCategory.INVALID_REQUEST),
        ],
    )
    def test_contract_does_not_retry_permanent_http_failures(
        self,
        status_code: int,
        category: NotificationErrorCategory,
    ) -> None:
        calls = 0

        def handle(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(status_code, json={"secret": "must-not-leak"})

        provider = self.make_provider(handle, sleep=lambda _: None)
        with pytest.raises(NotificationProviderError) as raised:
            provider.send(notification_payload(), delivery_key="key")

        assert calls == 1
        assert raised.value.category == category
        assert raised.value.retryable is False
        assert "must-not-leak" not in str(raised.value)

    @pytest.mark.parametrize(
        ("status_code", "category"),
        [
            (429, NotificationErrorCategory.RATE_LIMITED),
            (503, NotificationErrorCategory.UNAVAILABLE),
        ],
    )
    def test_contract_reports_exhausted_transient_http_failure(
        self,
        status_code: int,
        category: NotificationErrorCategory,
    ) -> None:
        calls = 0
        sleeps: list[float] = []

        def handle(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(status_code, json={"secret": "must-not-leak"})

        provider = self.make_provider(handle, sleep=sleeps.append)
        with pytest.raises(NotificationProviderError) as raised:
            provider.send(notification_payload(), delivery_key="key")

        assert calls == 2
        assert sleeps == [self.retry_backoff_seconds]
        assert raised.value.category == category
        assert raised.value.retryable is True
        assert raised.value.response_status == status_code
        assert "must-not-leak" not in str(raised.value)

    def test_contract_reports_exhausted_timeout_without_leaking_transport_error(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timeout with secret", request=request)

        provider = self.make_provider(handle, sleep=sleeps.append)
        with pytest.raises(NotificationProviderError) as raised:
            provider.send(notification_payload(), delivery_key="key")

        assert calls == 2
        assert sleeps == [self.retry_backoff_seconds]
        assert raised.value.category == NotificationErrorCategory.TIMEOUT
        assert raised.value.retryable is True
        assert "secret" not in str(raised.value)

    def test_contract_reports_exhausted_network_error_as_unavailable(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("network secret", request=request)

        provider = self.make_provider(handle, sleep=sleeps.append)
        with pytest.raises(NotificationProviderError) as raised:
            provider.send(notification_payload(), delivery_key="key")

        assert calls == 2
        assert sleeps == [self.retry_backoff_seconds]
        assert raised.value.category == NotificationErrorCategory.UNAVAILABLE
        assert raised.value.retryable is True
        assert "secret" not in str(raised.value)
