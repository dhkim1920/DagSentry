from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from dagsentry.config import Settings
from dagsentry.llm import AIDiagnosisResponse, LLMProvider, LLMProviderError
from dagsentry.providers import create_llm_provider
from dagsentry.providers.ollama import OllamaProvider, OllamaProviderConfig
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
    done: bool = True,
) -> dict[str, object]:
    return {
        "model": "qwen3:8b",
        "created_at": "2026-08-13T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": output_text or json.dumps(diagnosis_value()),
        },
        "done": done,
        "done_reason": "stop" if done else None,
        "total_duration": 123_000_000,
        "prompt_eval_count": 120,
        "eval_count": 45,
    }


class TestOllamaLLMProviderContract(LLMProviderContract):
    """Run the shared structured Diagnosis contract against Ollama Chat."""

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
                return httpx.Response(500, json={"error": "contract-secret"})
            if outcome == LLMContractOutcome.TIMEOUT:
                raise httpx.ReadTimeout("contract-secret", request=request)
            if outcome == LLMContractOutcome.NETWORK_ERROR:
                raise httpx.ConnectError("contract-secret", request=request)
            if outcome == LLMContractOutcome.PERMANENT_ERROR:
                return httpx.Response(404, json={"error": "contract-secret"})
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
            return httpx.Response(200, json=response_body(output_text))

        return OllamaProvider(
            OllamaProviderConfig(
                model="qwen3:8b",
                prompt_version="ai-diagnosis-v1",
                base_url="http://ollama.test/api",
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
        message = messages[1]
        assert isinstance(message, dict)
        content = message["content"]
        assert isinstance(content, str)
        context = json.loads(content)
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        body = _request_body(vendor_request)
        assert body["stream"] is False
        assert body["format"] == AIDiagnosisResponse.model_json_schema()


def _request_body(vendor_request: object) -> dict[str, Any]:
    assert isinstance(vendor_request, httpx.Request)
    value = json.loads(vendor_request.content)
    assert isinstance(value, dict)
    return value


def test_ollama_uses_chat_schema_and_deterministic_options() -> None:
    captured: httpx.Request | None = None

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=response_body())

    provider = OllamaProvider(
        OllamaProviderConfig(
            model="qwen3:8b",
            prompt_version="ai-diagnosis-v1",
            max_output_tokens=1_500,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    provider.diagnose(diagnosis_request())

    assert captured is not None
    assert captured.url == "http://localhost:11434/api/chat"
    assert "Authorization" not in captured.headers
    body = _request_body(captured)
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 1_500}
    assert body["format"] == AIDiagnosisResponse.model_json_schema()


def test_ollama_rejects_incomplete_non_streaming_response() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body(done=False))

    provider = OllamaProvider(
        OllamaProviderConfig(model="qwen3:8b", prompt_version="ai-diagnosis-v1"),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    with pytest.raises(LLMProviderError, match="invalid structured response"):
        provider.diagnose(diagnosis_request())


def test_ollama_factory_uses_local_endpoint_and_output_limit() -> None:
    settings = Settings(
        llm_provider="ollama",
        llm_model="qwen3:8b",
        ollama_api_base_url="http://ollama:11434/api",
        ollama_max_output_tokens=1_500,
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, OllamaProvider)
    assert provider.config.base_url == "http://ollama:11434/api"
    assert provider.config.max_output_tokens == 1_500


def test_ollama_settings_require_model() -> None:
    with pytest.raises(ValueError, match="model must be configured"):
        OllamaProvider.from_settings(Settings(llm_provider="ollama"))


@pytest.mark.parametrize(
    "base_url",
    ["localhost:11434/api", "file:///tmp/ollama.sock", ""],
)
def test_ollama_rejects_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\) URL"):
        OllamaProviderConfig(
            model="qwen3:8b",
            prompt_version="ai-diagnosis-v1",
            base_url=base_url,
        )
