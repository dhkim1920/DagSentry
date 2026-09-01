from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from dagsentry.ai_diagnosis import (
    AIDiagnosisOutcomeSource,
    RuleFallbackReason,
    diagnose_with_fallback,
)
from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision
from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    AIEvidence,
    ErrorSignatureContext,
    ExcerptLineContext,
    FailureMetadata,
    LLMCallMetadata,
    LLMProviderError,
    LLMResult,
    RuleDiagnosisContext,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.rule_diagnosis import RuleDiagnosisResult


def rule_result() -> RuleDiagnosisResult:
    return RuleDiagnosisResult(
        classification=ErrorClassification.DAG_CODE,
        matched_rule="python.application_exception.v1",
        ruleset_version=1,
        confidence=0.85,
        confidence_reason="Application exception",
        extracted_values=(),
        evidence_line_ids=(1,),
    )


def ai_request(*, has_log: bool = True) -> AIDiagnosisRequest:
    return AIDiagnosisRequest(
        metadata=FailureMetadata(
            environment="test",
            dag_id="orders",
            dag_run_id="run",
            task_id="load",
            map_index=-1,
            try_number=1,
            state="FAILED",
            observed_at=datetime.now(UTC),
            operator_type="PythonOperator",
        ),
        rule_diagnosis=RuleDiagnosisContext(
            classification=ErrorClassification.DAG_CODE,
            matched_rule="python.application_exception.v1",
            ruleset_version=1,
            confidence=0.85,
            confidence_reason="Application exception",
            extracted_values=(),
            evidence_line_ids=(1,),
        ),
        error_signature=ErrorSignatureContext(
            fingerprint="a" * 64,
            fingerprint_version=1,
            operator_type="PythonOperator",
            exception_class="ValueError",
            vendor_error_code=None,
            normalized_message="ValueError: invalid",
            application_stack_frame=None,
        ),
        excerpt=(ExcerptLineContext(1, "ValueError: invalid"),) if has_log else (),
    )


def llm_result() -> LLMResult:
    return LLMResult(
        diagnosis=AIDiagnosisResponse(
            classification=ErrorClassification.DAG_CODE,
            root_cause="Invalid input",
            confidence=0.9,
            evidence=[AIEvidence(line_id=1, text="ValueError: invalid")],
            recommended_actions=["Validate input"],
            retry_decision=RetryDecision.NOT_RETRYABLE,
            operator_review_required=True,
        ),
        metadata=LLMCallMetadata(
            request_id="req_1",
            model="test-model",
            prompt_version="ai-diagnosis-v1",
            latency_ms=10,
            input_tokens=10,
            output_tokens=5,
        ),
    )


@dataclass
class StubProvider:
    result: LLMResult | None = None
    error: LLMProviderError | None = None
    calls: int = 0

    def diagnose(self, _request: AIDiagnosisRequest) -> LLMResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_successful_provider_result_is_returned() -> None:
    provider = StubProvider(result=llm_result())

    outcome = diagnose_with_fallback(
        provider=provider,
        request=ai_request(),
        rule_diagnosis=rule_result(),
        log_processor=LogProcessor(),
    )

    assert outcome.source == AIDiagnosisOutcomeSource.AI
    assert outcome.ai_result == provider.result
    assert outcome.validation is not None
    assert outcome.fallback_reason is None
    assert provider.calls == 1


def test_unconfigured_provider_returns_rule_fallback() -> None:
    outcome = diagnose_with_fallback(
        provider=None,
        request=ai_request(),
        rule_diagnosis=rule_result(),
        log_processor=LogProcessor(),
    )

    assert outcome.source == AIDiagnosisOutcomeSource.RULE_FALLBACK
    assert outcome.ai_result is None
    assert outcome.fallback_reason == RuleFallbackReason.PROVIDER_NOT_CONFIGURED


def test_provider_failure_returns_rule_fallback() -> None:
    provider = StubProvider(error=LLMProviderError("unavailable"))

    outcome = diagnose_with_fallback(
        provider=provider,
        request=ai_request(),
        rule_diagnosis=rule_result(),
        log_processor=LogProcessor(),
    )

    assert outcome.source == AIDiagnosisOutcomeSource.RULE_FALLBACK
    assert outcome.fallback_reason == RuleFallbackReason.PROVIDER_ERROR
    assert provider.calls == 1


def test_missing_log_skips_provider_and_returns_rule_fallback() -> None:
    provider = StubProvider(result=llm_result())

    outcome = diagnose_with_fallback(
        provider=provider,
        request=ai_request(has_log=False),
        rule_diagnosis=rule_result(),
        log_processor=LogProcessor(),
    )

    assert outcome.source == AIDiagnosisOutcomeSource.RULE_FALLBACK
    assert outcome.fallback_reason == RuleFallbackReason.LOG_UNAVAILABLE
    assert provider.calls == 0


def test_invalid_evidence_is_not_exposed_as_effective_ai_result() -> None:
    invalid = llm_result()
    invalid.diagnosis.evidence[0] = AIEvidence(line_id=1, text="modified evidence")
    provider = StubProvider(result=invalid)

    outcome = diagnose_with_fallback(
        provider=provider,
        request=ai_request(),
        rule_diagnosis=rule_result(),
        log_processor=LogProcessor(),
    )

    assert outcome.source == AIDiagnosisOutcomeSource.RULE_FALLBACK
    assert outcome.fallback_reason == RuleFallbackReason.EVIDENCE_REJECTED
    assert outcome.ai_result is invalid
    assert outcome.validation is not None
