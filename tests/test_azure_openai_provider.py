from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.llm import AIDiagnosisResponse, LLMProvider
from dagsentry.providers import create_llm_provider
from dagsentry.providers.azure_openai import (
    AzureOpenAIProvider,
    AzureOpenAIProviderConfig,
)
from tests.contracts.llm import (
    LLMContractOutcome,
    LLMContractScenario,
    LLMProviderContract,
)


def diagnosis_value() -> dict[str, object]:
    return {
        "classification": "DAG_CODE",
        "root_cause": "The task rejected an invalid order.",
        "confidence": 0.91,
        "evidence": [{"line_id": 7, "text": "ValueError: invalid order"}],
        "recommended_actions": ["Validate the order input."],
        "retry_decision": "NOT_RETRYABLE",
        "operator_review_required": True,
    }


def response_body(output_text: str | None = None) -> dict[str, object]:
    return {
        "id": "resp_azure_contract",
        "model": "orders-diagnosis-deployment",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": output_text or json.dumps(diagnosis_value()),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 120, "output_tokens": 45},
    }


class TestAzureOpenAILLMProviderContract(LLMProviderContract):
    """Run the shared structured Diagnosis contract against Azure OpenAI v1."""

    def make_provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> LLMProvider:
        def handle(request: httpx.Request) -> httpx.Response:
            outcome = scenario.next(request)
            if outcome == LLMContractOutcome.RATE_LIMIT:
                return httpx.Response(429, json={"error": "contract-secret"})
            if outcome == LLMContractOutcome.SERVER_ERROR:
                return httpx.Response(503, json={"error": "contract-secret"})
            if outcome == LLMContractOutcome.TIMEOUT:
                raise httpx.ReadTimeout("contract-secret", request=request)
            if outcome == LLMContractOutcome.NETWORK_ERROR:
                raise httpx.ConnectError("contract-secret", request=request)
            if outcome == LLMContractOutcome.PERMANENT_ERROR:
                return httpx.Response(401, json={"error": "contract-secret"})
            if outcome == LLMContractOutcome.MALFORMED_RESPONSE:
                output_text = "{contract-secret"
            else:
                value = diagnosis_value()
                if outcome == LLMContractOutcome.INVALID_ENUM:
                    value["classification"] = "NOT_A_CLASSIFICATION"
                elif outcome == LLMContractOutcome.EXTRA_FIELD:
                    value["contract_secret"] = "contract-secret"
                elif outcome == LLMContractOutcome.INVALID_CONFIDENCE:
                    value["confidence"] = 2
                output_text = json.dumps(value)
            return httpx.Response(
                200,
                json=response_body(output_text),
                headers={"x-request-id": "req_azure_contract"},
            )

        return AzureOpenAIProvider(
            AzureOpenAIProviderConfig(
                api_key="azure-contract-secret",
                model="orders-diagnosis-deployment",
                prompt_version="ai-diagnosis-v1",
                base_url="https://example.openai.azure.com/openai/v1",
                max_attempts=2,
                retry_backoff_seconds=self.retry_backoff_seconds,
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
            sleep=sleep,
            monotonic=monotonic,
        )

    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        body = _request_body(vendor_request)
        serialized_input = body["input"]
        assert isinstance(serialized_input, str)
        context = json.loads(serialized_input)
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        body = _request_body(vendor_request)
        text = body["text"]
        assert isinstance(text, dict)
        response_format = text["format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
        assert response_format["strict"] is True
        assert response_format["schema"] == AIDiagnosisResponse.model_json_schema()


def _request_body(vendor_request: object) -> dict[str, Any]:
    assert isinstance(vendor_request, httpx.Request)
    value = json.loads(vendor_request.content)
    assert isinstance(value, dict)
    return value


def test_azure_openai_uses_v1_endpoint_and_api_key_header() -> None:
    captured: httpx.Request | None = None

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=response_body())

    provider = AzureOpenAIProvider(
        AzureOpenAIProviderConfig(
            api_key="azure-secret",
            model="orders-deployment",
            prompt_version="ai-diagnosis-v1",
            base_url="https://example.openai.azure.com/openai/v1",
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    from tests.contracts.llm import diagnosis_request

    provider.diagnose(diagnosis_request())

    assert captured is not None
    assert captured.url == "https://example.openai.azure.com/openai/v1/responses"
    assert captured.headers["api-key"] == "azure-secret"
    assert "Authorization" not in captured.headers
    assert _request_body(captured)["model"] == "orders-deployment"


def test_azure_openai_factory_normalizes_resource_endpoint_and_masks_key() -> None:
    settings = Settings(
        llm_provider="azure_openai",
        llm_model="orders-deployment",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key=SecretStr("azure-do-not-log"),
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, AzureOpenAIProvider)
    assert provider.config.base_url == "https://example.openai.azure.com/openai/v1"
    assert "azure-do-not-log" not in repr(provider.config)
    assert "**********" in repr(provider.config)


def test_azure_openai_settings_require_endpoint_key_and_deployment() -> None:
    with pytest.raises(ValueError, match="endpoint, API key, and deployment"):
        AzureOpenAIProvider.from_settings(Settings(llm_provider="azure_openai"))


def test_azure_openai_endpoint_requires_https() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        AzureOpenAIProviderConfig(
            api_key="key",
            model="deployment",
            prompt_version="ai-diagnosis-v1",
            base_url="http://example.openai.azure.com/openai/v1",
        )
