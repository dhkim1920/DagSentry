from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from dagsentry.config import Settings
from dagsentry.llm import LLMProvider, LLMProviderError
from dagsentry.providers import create_llm_provider
from dagsentry.providers.bedrock import (
    BedrockProvider,
    BedrockProviderConfig,
    _bedrock_diagnosis_schema,
)
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
) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": output_text or json.dumps(diagnosis_value())}],
            }
        },
        "stopReason": stop_reason,
        "usage": {"inputTokens": 120, "outputTokens": 45, "totalTokens": 165},
        "metrics": {"latencyMs": 100},
        "ResponseMetadata": {"RequestId": "req_bedrock_contract"},
    }


class ScenarioBedrockClient:
    def __init__(self, scenario: LLMContractScenario) -> None:
        self.scenario = scenario

    def converse(self, **kwargs: object) -> dict[str, Any]:
        outcome = self.scenario.next(kwargs)
        if outcome == LLMContractOutcome.RATE_LIMIT:
            raise _client_error("ThrottlingException")
        if outcome == LLMContractOutcome.SERVER_ERROR:
            raise _client_error("ServiceUnavailableException")
        if outcome == LLMContractOutcome.TIMEOUT:
            raise ReadTimeoutError(endpoint_url="https://bedrock.test", error="contract-secret")
        if outcome == LLMContractOutcome.NETWORK_ERROR:
            raise EndpointConnectionError(endpoint_url="https://contract-secret")
        if outcome == LLMContractOutcome.PERMANENT_ERROR:
            raise _client_error("AccessDeniedException")
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
        return response_body(output_text)


class TestBedrockLLMProviderContract(LLMProviderContract):
    """Run the shared structured Diagnosis contract against Bedrock Converse."""

    def make_provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> LLMProvider:
        return BedrockProvider(
            BedrockProviderConfig(
                region="ap-northeast-2",
                model_id="anthropic.claude-sonnet-4-6-v1:0",
                prompt_version="ai-diagnosis-v1",
                max_attempts=2,
                retry_backoff_seconds=self.retry_backoff_seconds,
            ),
            client=ScenarioBedrockClient(scenario),
            sleep=sleep,
            monotonic=monotonic,
        )

    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        request = _request_parameters(vendor_request)
        messages = request["messages"]
        assert isinstance(messages, list)
        message = messages[0]
        assert isinstance(message, dict)
        content = message["content"]
        assert isinstance(content, list)
        block = content[0]
        assert isinstance(block, dict)
        text = block["text"]
        assert isinstance(text, str)
        context = json.loads(text)
        assert isinstance(context, dict)
        return context

    def assert_structured_output_requested(self, vendor_request: object) -> None:
        request = _request_parameters(vendor_request)
        output_config = request["outputConfig"]
        assert isinstance(output_config, dict)
        text_format = output_config["textFormat"]
        assert isinstance(text_format, dict)
        assert text_format["type"] == "json_schema"
        structure = text_format["structure"]
        assert isinstance(structure, dict)
        json_schema = structure["jsonSchema"]
        assert isinstance(json_schema, dict)
        schema = json_schema["schema"]
        assert isinstance(schema, str)
        assert json.loads(schema) == _bedrock_diagnosis_schema()


def _request_parameters(vendor_request: object) -> dict[str, object]:
    assert isinstance(vendor_request, dict)
    return vendor_request


def _client_error(code: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "contract-secret"},
            "ResponseMetadata": {"RequestId": "req_bedrock_error"},
        },
        "Converse",
    )


def test_bedrock_uses_converse_shape_and_model_id() -> None:
    scenario = LLMContractScenario([LLMContractOutcome.SUCCESS])
    provider = BedrockProvider(
        BedrockProviderConfig(
            region="ap-northeast-2",
            model_id="anthropic.claude-sonnet-4-6-v1:0",
            prompt_version="ai-diagnosis-v1",
            max_output_tokens=1_500,
        ),
        client=ScenarioBedrockClient(scenario),
    )

    provider.diagnose(diagnosis_request())

    request = _request_parameters(scenario.captured_requests[0])
    assert request["modelId"] == "anthropic.claude-sonnet-4-6-v1:0"
    assert request["inferenceConfig"] == {"maxTokens": 1_500}
    assert request["system"]


def test_bedrock_schema_removes_unsupported_numeric_and_string_constraints() -> None:
    serialized = json.dumps(_bedrock_diagnosis_schema())

    for unsupported in ("minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength"):
        assert f'"{unsupported}"' not in serialized
    assert '"additionalProperties": false' in serialized


@pytest.mark.parametrize("stop_reason", ["max_tokens", "guardrail_intervened"])
def test_bedrock_rejects_incomplete_or_intervened_output(stop_reason: str) -> None:
    class StoppedClient:
        def converse(self, **_kwargs: object) -> dict[str, Any]:
            return response_body("contract-secret", stop_reason=stop_reason)

    provider = BedrockProvider(
        BedrockProviderConfig(
            region="ap-northeast-2",
            model_id="model-id",
            prompt_version="ai-diagnosis-v1",
        ),
        client=StoppedClient(),
    )

    with pytest.raises(LLMProviderError, match="invalid structured response") as raised:
        provider.diagnose(diagnosis_request())

    assert "contract-secret" not in str(raised.value)


def test_bedrock_missing_credentials_are_permanent_and_sanitized() -> None:
    class MissingCredentialsClient:
        def converse(self, **_kwargs: object) -> dict[str, Any]:
            raise NoCredentialsError()

    provider = BedrockProvider(
        BedrockProviderConfig(
            region="ap-northeast-2",
            model_id="model-id",
            prompt_version="ai-diagnosis-v1",
        ),
        client=MissingCredentialsClient(),
    )

    with pytest.raises(LLMProviderError, match="credentials are unavailable"):
        provider.diagnose(diagnosis_request())


def test_bedrock_factory_uses_region_model_and_standard_credential_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_config: BedrockProviderConfig | None = None

    def fake_client(config: BedrockProviderConfig) -> ScenarioBedrockClient:
        nonlocal captured_config
        captured_config = config
        return ScenarioBedrockClient(LLMContractScenario([LLMContractOutcome.SUCCESS]))

    monkeypatch.setattr("dagsentry.providers.bedrock._create_client", fake_client)
    settings = Settings(
        llm_provider="bedrock",
        llm_model="anthropic.claude-sonnet-4-6-v1:0",
        bedrock_region="ap-northeast-2",
        bedrock_max_output_tokens=1_500,
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, BedrockProvider)
    assert captured_config is not None
    assert captured_config.region == "ap-northeast-2"
    assert captured_config.model_id == "anthropic.claude-sonnet-4-6-v1:0"
    assert captured_config.max_output_tokens == 1_500


def test_bedrock_settings_require_region_and_model() -> None:
    with pytest.raises(ValueError, match="region and model ID"):
        BedrockProvider.from_settings(Settings(llm_provider="bedrock"))
