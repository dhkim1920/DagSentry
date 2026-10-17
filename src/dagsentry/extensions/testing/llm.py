"""Reusable contract tests for structured Diagnosis LLM Providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import httpx
import pytest

from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    AIEvidence,
    ErrorSignatureContext,
    ExcerptLineContext,
    FailureMetadata,
    LLMProvider,
    LLMProviderError,
    RuleDiagnosisContext,
)
from dagsentry.prompts import AI_DIAGNOSIS_INSTRUCTIONS


class LLMContractOutcome(StrEnum):
    """Provider-neutral fake outcomes consumed by concrete contract harnesses."""

    SUCCESS = "SUCCESS"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PERMANENT_ERROR = "PERMANENT_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    INVALID_ENUM = "INVALID_ENUM"
    EXTRA_FIELD = "EXTRA_FIELD"
    INVALID_CONFIDENCE = "INVALID_CONFIDENCE"


@dataclass
class LLMContractScenario:
    """Ordered fake Provider outcomes and captured vendor requests."""

    outcomes: list[LLMContractOutcome]
    captured_requests: list[object] = field(default_factory=list)
    calls: int = 0

    def next(self, vendor_request: object) -> LLMContractOutcome:
        """Capture one request and return its configured outcome."""
        if self.calls >= len(self.outcomes):
            raise AssertionError("LLM contract scenario received an unexpected call")
        self.captured_requests.append(vendor_request)
        outcome = self.outcomes[self.calls]
        self.calls += 1
        return outcome


def diagnosis_request() -> AIDiagnosisRequest:
    """Return the complete bounded Core request used by every Provider contract."""
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


def expected_diagnosis() -> AIDiagnosisResponse:
    """Return the valid structured response emitted by a successful fake."""
    return AIDiagnosisResponse(
        classification=ErrorClassification.DAG_CODE,
        root_cause="The task rejected an invalid order.",
        confidence=0.91,
        evidence=[AIEvidence(line_id=7, text="ValueError: invalid order")],
        recommended_actions=["Validate the order input."],
        retry_decision=RetryDecision.NOT_RETRYABLE,
        operator_review_required=True,
    )


class LLMProviderContract(ABC):
    """Core-visible behavior every structured Diagnosis Provider must pass."""

    retry_backoff_seconds = 0.25

    @abstractmethod
    def make_provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> LLMProvider:
        """Build a two-attempt concrete Provider backed by the scenario."""

    @abstractmethod
    def extract_request_context(self, vendor_request: object) -> dict[str, object]:
        """Extract the Core JSON context from one captured vendor request."""

    @abstractmethod
    def assert_structured_output_requested(self, vendor_request: object) -> None:
        """Verify the vendor request enforces the strict Diagnosis shape."""

    def _provider(
        self,
        scenario: LLMContractScenario,
        *,
        sleep: Callable[[float], None] = lambda _: None,
        monotonic: Callable[[], float] = lambda: 10.0,
    ) -> LLMProvider:
        return self.make_provider(scenario, sleep=sleep, monotonic=monotonic)

    def test_contract_uses_exact_allowlisted_context_and_structured_output(self) -> None:
        scenario = LLMContractScenario([LLMContractOutcome.SUCCESS])
        request = diagnosis_request()

        result = self._provider(scenario).diagnose(request)

        assert result.diagnosis == expected_diagnosis()
        assert scenario.calls == 1
        captured = scenario.captured_requests[0]
        expected_context = json.loads(json.dumps(request.to_json_value()))
        assert self.extract_request_context(captured) == expected_context
        self.assert_structured_output_requested(captured)
        body = (
            captured.content.decode()
            if isinstance(captured, httpx.Request)
            else json.dumps(captured)
        )
        assert AI_DIAGNOSIS_INSTRUCTIONS in body
        assert "Korean" in AI_DIAGNOSIS_INSTRUCTIONS
        assert "Do not translate, trim, or change whitespace" in AI_DIAGNOSIS_INSTRUCTIONS

    def test_contract_returns_non_secret_call_metadata(self) -> None:
        scenario = LLMContractScenario([LLMContractOutcome.SUCCESS])
        times = iter((10.0, 10.123))

        result = self._provider(scenario, monotonic=lambda: next(times)).diagnose(
            diagnosis_request()
        )

        assert result.metadata.model
        assert result.metadata.prompt_version
        assert result.metadata.latency_ms == 123
        assert result.metadata.input_tokens is None or result.metadata.input_tokens >= 0
        assert result.metadata.output_tokens is None or result.metadata.output_tokens >= 0

    @pytest.mark.parametrize(
        "first_outcome",
        [
            LLMContractOutcome.RATE_LIMIT,
            LLMContractOutcome.SERVER_ERROR,
            LLMContractOutcome.TIMEOUT,
            LLMContractOutcome.NETWORK_ERROR,
        ],
    )
    def test_contract_retries_transient_failure_then_succeeds(
        self,
        first_outcome: LLMContractOutcome,
    ) -> None:
        scenario = LLMContractScenario([first_outcome, LLMContractOutcome.SUCCESS])
        sleeps: list[float] = []

        result = self._provider(scenario, sleep=sleeps.append).diagnose(diagnosis_request())

        assert result.diagnosis == expected_diagnosis()
        assert scenario.calls == 2
        assert sleeps == [self.retry_backoff_seconds]

    def test_contract_does_not_retry_or_leak_permanent_failure(self) -> None:
        scenario = LLMContractScenario([LLMContractOutcome.PERMANENT_ERROR])

        with pytest.raises(LLMProviderError) as raised:
            self._provider(scenario).diagnose(diagnosis_request())

        assert scenario.calls == 1
        assert "contract-secret" not in str(raised.value)

    @pytest.mark.parametrize(
        "outcome",
        [
            LLMContractOutcome.RATE_LIMIT,
            LLMContractOutcome.SERVER_ERROR,
            LLMContractOutcome.TIMEOUT,
            LLMContractOutcome.NETWORK_ERROR,
        ],
    )
    def test_contract_reports_exhausted_transient_failure_without_leak(
        self,
        outcome: LLMContractOutcome,
    ) -> None:
        scenario = LLMContractScenario([outcome, outcome])
        sleeps: list[float] = []

        with pytest.raises(LLMProviderError) as raised:
            self._provider(scenario, sleep=sleeps.append).diagnose(diagnosis_request())

        assert scenario.calls == 2
        assert sleeps == [self.retry_backoff_seconds]
        assert "contract-secret" not in str(raised.value)

    @pytest.mark.parametrize(
        "outcome",
        [
            LLMContractOutcome.MALFORMED_RESPONSE,
            LLMContractOutcome.INVALID_ENUM,
            LLMContractOutcome.EXTRA_FIELD,
            LLMContractOutcome.INVALID_CONFIDENCE,
        ],
    )
    def test_contract_rejects_invalid_structured_response(
        self,
        outcome: LLMContractOutcome,
    ) -> None:
        scenario = LLMContractScenario([outcome])

        with pytest.raises(LLMProviderError) as raised:
            self._provider(scenario).diagnose(diagnosis_request())

        assert scenario.calls == 1
        assert "contract-secret" not in str(raised.value)
