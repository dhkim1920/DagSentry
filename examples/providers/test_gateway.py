"""Run with: python -m pytest examples/providers/test_gateway.py -q (no network)."""

import json
from collections.abc import Callable

import httpx

from dagsentry.extensions import AIDiagnosisResponse, NotificationPayload
from dagsentry.extensions.testing.llm import (
    LLMContractOutcome,
    LLMContractScenario,
    LLMProviderContract,
    expected_diagnosis,
)
from dagsentry.extensions.testing.notification import NotificationProviderContract
from examples.providers.gateway import GatewayLLMProvider, GatewayNotificationProvider, _Gateway


class TestGatewayLLM(LLMProviderContract):
    def make_provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> GatewayLLMProvider:
        def handle(request: httpx.Request) -> httpx.Response:
            outcome = scenario.next(request)
            if outcome == LLMContractOutcome.TIMEOUT:
                raise httpx.ReadTimeout("contract-secret")
            if outcome == LLMContractOutcome.NETWORK_ERROR:
                raise httpx.ConnectError("contract-secret")
            if outcome in {
                LLMContractOutcome.RATE_LIMIT,
                LLMContractOutcome.SERVER_ERROR,
                LLMContractOutcome.PERMANENT_ERROR,
            }:
                return httpx.Response(
                    {
                        LLMContractOutcome.RATE_LIMIT: 429,
                        LLMContractOutcome.SERVER_ERROR: 503,
                        LLMContractOutcome.PERMANENT_ERROR: 401,
                    }[outcome]
                )
            if outcome == LLMContractOutcome.MALFORMED_RESPONSE:
                return httpx.Response(200, content="{contract-secret")
            value = expected_diagnosis().model_dump(mode="json")
            if outcome == LLMContractOutcome.INVALID_ENUM:
                value["classification"] = "INVALID"
            if outcome == LLMContractOutcome.EXTRA_FIELD:
                value["unexpected"] = "contract-secret"
            if outcome == LLMContractOutcome.INVALID_CONFIDENCE:
                value["confidence"] = 2
            return httpx.Response(200, json=value)

        return GatewayLLMProvider(
            _Gateway(
                "https://gateway.example/diagnose",
                "contract-secret",
                client=httpx.Client(transport=httpx.MockTransport(handle)),
                sleep=sleep,
                backoff=self.retry_backoff_seconds,
            ),
            model="company-model",
            prompt_version="company-v1",
            monotonic=monotonic,
        )

    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        assert isinstance(vendor_request, httpx.Request)
        context = json.loads(vendor_request.content)["context"]
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        assert isinstance(vendor_request, httpx.Request)
        assert (
            json.loads(vendor_request.content)["schema"] == AIDiagnosisResponse.model_json_schema()
        )


class TestGatewayNotification(NotificationProviderContract):
    def make_provider(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        sleep: Callable[[float], None],
    ) -> GatewayNotificationProvider:
        return GatewayNotificationProvider(
            _Gateway(
                "https://gateway.example/notify",
                "contract-secret",
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                sleep=sleep,
                backoff=self.retry_backoff_seconds,
            )
        )

    def assert_success_request(
        self,
        request: httpx.Request,
        payload: NotificationPayload,
        delivery_key: str,
    ) -> None:
        assert request.headers["Idempotency-Key"] == delivery_key
        assert json.loads(request.content) == payload.model_dump(mode="json")
