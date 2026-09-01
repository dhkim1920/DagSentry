from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.llm import AIDiagnosisResponse, LLMProvider, LLMProviderError
from dagsentry.providers import create_llm_provider
from dagsentry.providers.anthropic import AnthropicProvider, AnthropicProviderConfig
from tests.contracts.llm import (
    LLMContractOutcome,
    LLMContractScenario,
    LLMProviderContract,
    diagnosis_request,
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


def response_body(
    output_text: str | None = None,
    *,
    stop_reason: str = "end_turn",
) -> dict[str, object]:
    return {
        "id": "msg_anthropic_contract",
        "model": "claude-sonnet-test",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": output_text or json.dumps(diagnosis_value()),
            }
        ],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 120, "output_tokens": 45},
    }


class TestAnthropicLLMProviderContract(LLMProviderContract):
    """Run the shared structured Diagnosis contract against Anthropic Messages."""

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
                return httpx.Response(529, json={"error": "contract-secret"})
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
                headers={"request-id": "req_anthropic_contract"},
            )

        return AnthropicProvider(
            AnthropicProviderConfig(
                api_key="anthropic-contract-secret",
                model="claude-sonnet-test",
                prompt_version="ai-diagnosis-v1",
                base_url="https://anthropic.test/v1",
                max_attempts=2,
                retry_backoff_seconds=self.retry_backoff_seconds,
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
            sleep=sleep,
            monotonic=monotonic,
        )

    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        body = _request_body(vendor_request)
        messages = body["messages"]
        assert isinstance(messages, list)
        message = messages[0]
        assert isinstance(message, dict)
        content = message["content"]
        assert isinstance(content, str)
        context = json.loads(content)
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        body = _request_body(vendor_request)
        output_config = body["output_config"]
        assert isinstance(output_config, dict)
        response_format = output_config["format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
        assert response_format["schema"] == AIDiagnosisResponse.model_json_schema()


def _request_body(vendor_request: object) -> dict[str, Any]:
    assert isinstance(vendor_request, httpx.Request)
    value = json.loads(vendor_request.content)
    assert isinstance(value, dict)
    return value


def test_anthropic_uses_messages_headers_and_output_limit() -> None:
    captured: httpx.Request | None = None

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=response_body())

    provider = AnthropicProvider(
        AnthropicProviderConfig(
            api_key="anthropic-secret",
            model="claude-sonnet-test",
            prompt_version="ai-diagnosis-v1",
            max_output_tokens=1_500,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    provider.diagnose(diagnosis_request())

    assert captured is not None
    assert captured.url == "https://api.anthropic.com/v1/messages"
    assert captured.headers["x-api-key"] == "anthropic-secret"
    assert captured.headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured.headers
    assert _request_body(captured)["max_tokens"] == 1_500


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_rejects_incomplete_or_refused_structured_output(stop_reason: str) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response_body("contract-secret refusal", stop_reason=stop_reason),
        )

    provider = AnthropicProvider(
        AnthropicProviderConfig(
            api_key="anthropic-secret",
            model="claude-sonnet-test",
            prompt_version="ai-diagnosis-v1",
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    with pytest.raises(LLMProviderError, match="invalid structured response") as raised:
        provider.diagnose(diagnosis_request())

    assert "contract-secret" not in str(raised.value)


def test_anthropic_honors_bounded_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "120"})
        return httpx.Response(200, json=response_body())

    provider = AnthropicProvider(
        AnthropicProviderConfig(
            api_key="anthropic-secret",
            model="claude-sonnet-test",
            prompt_version="ai-diagnosis-v1",
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        sleep=sleeps.append,
    )

    provider.diagnose(diagnosis_request())

    assert sleeps == [60.0]


def test_anthropic_factory_and_masked_config() -> None:
    settings = Settings(
        llm_provider="anthropic",
        llm_model="claude-sonnet-test",
        anthropic_api_key=SecretStr("anthropic-do-not-log"),
        anthropic_max_output_tokens=1_500,
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, AnthropicProvider)
    assert provider.config.max_output_tokens == 1_500
    assert "anthropic-do-not-log" not in repr(provider.config)
    assert "**********" in repr(provider.config)


def test_anthropic_settings_require_api_key_and_model() -> None:
    with pytest.raises(ValueError, match="API key and model"):
        AnthropicProvider.from_settings(Settings(llm_provider="anthropic"))


def test_anthropic_requires_https_for_public_endpoint() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        AnthropicProviderConfig(
            api_key="secret",
            model="model",
            prompt_version="v1",
            base_url="http://api.anthropic.example/v1",
        )
