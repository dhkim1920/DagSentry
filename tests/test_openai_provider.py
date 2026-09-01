from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from dagsentry.config import Settings
from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    ErrorSignatureContext,
    ExcerptLineContext,
    FailureMetadata,
    LLMProviderError,
    RuleDiagnosisContext,
)
from dagsentry.providers import create_llm_provider
from dagsentry.providers.openai import OpenAIProvider, OpenAIProviderConfig
from tests.contracts.llm import (
    LLMContractOutcome,
    LLMContractScenario,
    LLMProviderContract,
)


def request_context() -> AIDiagnosisRequest:
    return AIDiagnosisRequest(
        metadata=FailureMetadata(
            environment="production",
            dag_id="orders",
            dag_run_id="scheduled__2026-08-10",
            task_id="load",
            map_index=-1,
            try_number=1,
            state="FAILED",
            observed_at=datetime(2026, 8, 10, tzinfo=UTC),
            operator_type="PythonOperator",
        ),
        rule_diagnosis=RuleDiagnosisContext(
            classification=ErrorClassification.DAG_CODE,
            matched_rule="python.application_exception.v1",
            ruleset_version=1,
            confidence=0.85,
            confidence_reason="Application exception",
            extracted_values=({"name": "exception_class", "value": "ValueError"},),
            evidence_line_ids=(7,),
        ),
        error_signature=ErrorSignatureContext(
            fingerprint="a" * 64,
            fingerprint_version=1,
            operator_type="PythonOperator",
            exception_class="ValueError",
            vendor_error_code=None,
            normalized_message="ValueError: invalid order",
            application_stack_frame=None,
        ),
        excerpt=(ExcerptLineContext(7, "ValueError: invalid order"),),
    )


def response_body() -> dict[str, object]:
    return {
        "id": "resp_123",
        "model": "test-model-2026-08-10",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "classification": "DAG_CODE",
                                "root_cause": "The task rejected an invalid order.",
                                "confidence": 0.91,
                                "evidence": [{"line_id": 7, "text": "ValueError: invalid order"}],
                                "recommended_actions": ["Validate the order input."],
                                "retry_decision": "NOT_RETRYABLE",
                                "operator_review_required": True,
                            }
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 120, "output_tokens": 45},
    }


def provider(
    handler: httpx.MockTransport,
    *,
    api_key: str = "top-secret-key",
    max_attempts: int = 2,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> OpenAIProvider:
    return OpenAIProvider(
        OpenAIProviderConfig(
            api_key=api_key,
            model="test-model",
            prompt_version="ai-diagnosis-v1",
            base_url="https://llm.test/v1",
            max_attempts=max_attempts,
            retry_backoff_seconds=0.25,
        ),
        http_client=httpx.Client(transport=handler),
        sleep=sleep or time.sleep,
        monotonic=monotonic or time.monotonic,
    )


class TestOpenAILLMProviderContract(LLMProviderContract):
    """Run the shared structured Diagnosis contract against OpenAI Responses."""

    def make_provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> OpenAIProvider:
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

            body = response_body()
            if outcome == LLMContractOutcome.MALFORMED_RESPONSE:
                _set_output_text(body, "{contract-secret")
            elif outcome == LLMContractOutcome.INVALID_ENUM:
                value = _diagnosis_json_value()
                value["classification"] = "NOT_A_CLASSIFICATION"
                _set_output_text(body, json.dumps(value))
            elif outcome == LLMContractOutcome.EXTRA_FIELD:
                value = _diagnosis_json_value()
                value["contract_secret"] = "contract-secret"
                _set_output_text(body, json.dumps(value))
            elif outcome == LLMContractOutcome.INVALID_CONFIDENCE:
                value = _diagnosis_json_value()
                value["confidence"] = 2
                _set_output_text(body, json.dumps(value))
            return httpx.Response(
                200,
                json=body,
                headers={"x-request-id": "req_contract"},
            )

        return provider(
            httpx.MockTransport(handle),
            sleep=sleep,
            monotonic=monotonic,
        )

    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        assert isinstance(vendor_request, httpx.Request)
        body = json.loads(vendor_request.content)
        assert isinstance(body, dict)
        serialized_input = body["input"]
        assert isinstance(serialized_input, str)
        context = json.loads(serialized_input)
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        assert isinstance(vendor_request, httpx.Request)
        body = json.loads(vendor_request.content)
        assert isinstance(body, dict)
        text = body["text"]
        assert isinstance(text, dict)
        response_format = text["format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
        assert response_format["strict"] is True
        assert response_format["schema"] == AIDiagnosisResponse.model_json_schema()


def _diagnosis_json_value() -> dict[str, object]:
    output = response_body()["output"]
    assert isinstance(output, list)
    message = output[0]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    item = content[0]
    assert isinstance(item, dict)
    text = item["text"]
    assert isinstance(text, str)
    value = json.loads(text)
    assert isinstance(value, dict)
    return value


def _set_output_text(body: dict[str, object], text: str) -> None:
    output = body["output"]
    assert isinstance(output, list)
    message = output[0]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    item = content[0]
    assert isinstance(item, dict)
    item["text"] = text


def test_requests_strict_json_schema_with_only_allowlisted_context() -> None:
    captured_body: dict[str, object] = {}
    raw_log = "raw prefix SECRET-OUTSIDE-EXCERPT ValueError: invalid order raw suffix"
    incident_history = "INCIDENT-HISTORY-MUST-NOT-BE-SENT"

    def handle(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(),
            headers={"x-request-id": "req_123"},
        )

    result = provider(httpx.MockTransport(handle)).diagnose(request_context())

    assert result.diagnosis.classification == ErrorClassification.DAG_CODE
    assert result.diagnosis.retry_decision == RetryDecision.NOT_RETRYABLE
    assert captured_body["model"] == "test-model"
    assert captured_body["store"] is False
    text_config = captured_body["text"]
    assert isinstance(text_config, dict)
    response_format = text_config["format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    serialized_input = captured_body["input"]
    assert isinstance(serialized_input, str)
    assert "ValueError: invalid order" in serialized_input
    assert raw_log not in serialized_input
    assert "SECRET-OUTSIDE-EXCERPT" not in serialized_input
    assert incident_history not in serialized_input
    assert "raw_log" not in serialized_input
    assert "incident" not in serialized_input


@pytest.mark.parametrize("transient_failure", ["rate_limit", "server_error", "timeout"])
def test_retries_transient_failures_then_succeeds(transient_failure: str) -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if transient_failure == "rate_limit":
                return httpx.Response(429, json={"error": "limited"})
            if transient_failure == "server_error":
                return httpx.Response(503, json={"error": "unavailable"})
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=response_body())

    result = provider(
        httpx.MockTransport(handle),
        sleep=sleeps.append,
    ).diagnose(request_context())

    assert result.diagnosis.root_cause == "The task rejected an invalid order."
    assert calls == 2
    assert sleeps == [0.25]


def test_permanent_http_failure_is_not_retried_or_leaked(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "top-secret-key"})

    caplog.set_level(logging.WARNING)
    openai = provider(httpx.MockTransport(handle))

    with pytest.raises(LLMProviderError, match="HTTP 401"):
        openai.diagnose(request_context())

    assert calls == 1
    assert "top-secret-key" not in caplog.text
    assert "http_401" in caplog.text
    assert "top-secret-key" not in repr(openai.config)


def test_records_non_secret_call_metadata(caplog: pytest.LogCaptureFixture) -> None:
    times: Iterator[float] = iter((10.0, 10.123))

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response_body(),
            headers={"x-request-id": "req_abc"},
        )

    caplog.set_level(logging.INFO)
    result = provider(
        httpx.MockTransport(handle),
        monotonic=lambda: next(times),
    ).diagnose(request_context())

    assert result.metadata.request_id == "req_abc"
    assert result.metadata.model == "test-model-2026-08-10"
    assert result.metadata.prompt_version == "ai-diagnosis-v1"
    assert result.metadata.latency_ms == 123
    assert result.metadata.input_tokens == 120
    assert result.metadata.output_tokens == 45
    assert "request_id=req_abc" in caplog.text
    assert "input_tokens=120" in caplog.text
    assert "top-secret-key" not in caplog.text


def test_invalid_structured_response_raises_sanitized_error() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        body = response_body()
        body["output"] = []
        return httpx.Response(200, json=body)

    with pytest.raises(LLMProviderError, match="invalid structured response"):
        provider(httpx.MockTransport(handle)).diagnose(request_context())


@pytest.mark.parametrize(
    "output_text",
    [
        "{malformed-json",
        json.dumps(
            {
                "classification": "NOT_A_CLASSIFICATION",
                "root_cause": "Invalid input",
                "confidence": 0.9,
                "evidence": [{"line_id": 7, "text": "ValueError: invalid order"}],
                "recommended_actions": ["Validate input"],
                "retry_decision": "NOT_RETRYABLE",
                "operator_review_required": True,
            }
        ),
    ],
    ids=["malformed-json", "invalid-classification"],
)
def test_malformed_or_invalid_enum_response_is_rejected(output_text: str) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        body = response_body()
        output = body["output"]
        assert isinstance(output, list)
        message = output[0]
        assert isinstance(message, dict)
        content = message["content"]
        assert isinstance(content, list)
        output_item = content[0]
        assert isinstance(output_item, dict)
        output_item["text"] = output_text
        return httpx.Response(200, json=body)

    with pytest.raises(LLMProviderError, match="invalid structured response"):
        provider(httpx.MockTransport(handle)).diagnose(request_context())


def test_provider_can_be_enabled_from_settings() -> None:
    settings = Settings(
        llm_provider="openai",
        llm_model="test-model",
        openai_api_key=SecretStr("secret"),
    )

    openai = OpenAIProvider.from_settings(settings)

    assert openai.config.model == "test-model"
    assert openai.config.prompt_version == "ai-diagnosis-v1"
    assert "secret" not in repr(openai.config)


def test_incomplete_or_disabled_settings_do_not_create_provider() -> None:
    assert create_llm_provider(Settings()) is None
    with pytest.raises(ValueError, match="not enabled"):
        OpenAIProvider.from_settings(Settings())
    with pytest.raises(ValueError, match="must be configured"):
        OpenAIProvider.from_settings(Settings(llm_provider="openai"))


def test_openai_requires_https_except_for_localhost_or_explicitly_trusted_host() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        OpenAIProviderConfig(
            api_key="secret",
            model="model",
            prompt_version="v1",
            base_url="http://api.openai.example/v1",
        )

    OpenAIProviderConfig(
        api_key="secret",
        model="model",
        prompt_version="v1",
        base_url="http://localhost:8080/v1",
    )
    OpenAIProviderConfig(
        api_key="secret",
        model="model",
        prompt_version="v1",
        base_url="http://10.0.0.15/v1",
        trusted_http_hosts=frozenset({"10.0.0.15"}),
    )
