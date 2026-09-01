"""AI Diagnosis orchestration with deterministic Rule fallback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dagsentry.domain.diagnosis import DiagnosisValidationStatus
from dagsentry.evidence_validation import AIEvidenceValidationResult, validate_ai_evidence
from dagsentry.llm import AIDiagnosisRequest, LLMProvider, LLMProviderError, LLMResult
from dagsentry.log_processing import LogProcessor
from dagsentry.rule_diagnosis import RuleDiagnosisResult


class AIDiagnosisOutcomeSource(StrEnum):
    """Stage that produced the effective diagnosis result."""

    AI = "AI"
    RULE_FALLBACK = "RULE_FALLBACK"


class RuleFallbackReason(StrEnum):
    """Why AI was safely skipped or abandoned."""

    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    LOG_UNAVAILABLE = "LOG_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    EVIDENCE_REJECTED = "EVIDENCE_REJECTED"


@dataclass(frozen=True)
class AIDiagnosisOutcome:
    """Either an AI result or the always-available deterministic fallback."""

    source: AIDiagnosisOutcomeSource
    rule_diagnosis: RuleDiagnosisResult
    ai_result: LLMResult | None
    validation: AIEvidenceValidationResult | None
    fallback_reason: RuleFallbackReason | None


def diagnose_with_fallback(
    *,
    provider: LLMProvider | None,
    request: AIDiagnosisRequest,
    rule_diagnosis: RuleDiagnosisResult,
    log_processor: LogProcessor,
) -> AIDiagnosisOutcome:
    """Call AI only with log evidence and convert Provider failures to Rule fallback."""
    if not request.excerpt:
        return _fallback(rule_diagnosis, RuleFallbackReason.LOG_UNAVAILABLE)
    if provider is None:
        return _fallback(rule_diagnosis, RuleFallbackReason.PROVIDER_NOT_CONFIGURED)

    try:
        result = provider.diagnose(request)
    except LLMProviderError:
        return _fallback(rule_diagnosis, RuleFallbackReason.PROVIDER_ERROR)
    validation = validate_ai_evidence(
        result.diagnosis,
        request.excerpt,
        log_processor=log_processor,
    )
    if validation.status == DiagnosisValidationStatus.REJECTED:
        return AIDiagnosisOutcome(
            source=AIDiagnosisOutcomeSource.RULE_FALLBACK,
            rule_diagnosis=rule_diagnosis,
            ai_result=result,
            validation=validation,
            fallback_reason=RuleFallbackReason.EVIDENCE_REJECTED,
        )
    return AIDiagnosisOutcome(
        source=AIDiagnosisOutcomeSource.AI,
        rule_diagnosis=rule_diagnosis,
        ai_result=result,
        validation=validation,
        fallback_reason=None,
    )


def _fallback(
    rule_diagnosis: RuleDiagnosisResult,
    reason: RuleFallbackReason,
) -> AIDiagnosisOutcome:
    return AIDiagnosisOutcome(
        source=AIDiagnosisOutcomeSource.RULE_FALLBACK,
        rule_diagnosis=rule_diagnosis,
        ai_result=None,
        validation=None,
        fallback_reason=reason,
    )
