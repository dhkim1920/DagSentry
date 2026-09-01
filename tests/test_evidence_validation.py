from __future__ import annotations

from dagsentry.domain.diagnosis import (
    DiagnosisValidationStatus,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.evidence_validation import (
    AIEvidenceValidationResult,
    EvidenceRejectionCode,
    validate_ai_evidence,
)
from dagsentry.llm import AIDiagnosisResponse, AIEvidence
from dagsentry.log_processing import LogProcessor, RelevantLogExcerpt


def ai_response(
    *,
    evidence: list[AIEvidence] | None = None,
    root_cause: str = "The task rejected an invalid order.",
    recommended_actions: list[str] | None = None,
) -> AIDiagnosisResponse:
    return AIDiagnosisResponse(
        classification=ErrorClassification.DAG_CODE,
        root_cause=root_cause,
        confidence=0.91,
        evidence=(
            evidence
            if evidence is not None
            else [AIEvidence(line_id=2, text="ValueError: invalid order")]
        ),
        recommended_actions=recommended_actions or ["Validate the order input."],
        retry_decision=RetryDecision.NOT_RETRYABLE,
        operator_review_required=True,
    )


def excerpt() -> RelevantLogExcerpt:
    return LogProcessor().process("Traceback (most recent call last):\nValueError: invalid order")


def rejection_codes(result: AIEvidenceValidationResult) -> set[EvidenceRejectionCode]:
    return {rejection.code for rejection in result.rejections}


def test_exact_existing_evidence_passes() -> None:
    processor = LogProcessor()
    result = validate_ai_evidence(ai_response(), excerpt().lines, log_processor=processor)

    assert result.status == DiagnosisValidationStatus.PASSED
    assert result.rejections == ()


def test_unknown_line_id_is_rejected() -> None:
    result = validate_ai_evidence(
        ai_response(evidence=[AIEvidence(line_id=99, text="ValueError: invalid order")]),
        excerpt().lines,
        log_processor=LogProcessor(),
    )

    assert result.status == DiagnosisValidationStatus.REJECTED
    assert EvidenceRejectionCode.UNKNOWN_LINE_ID in rejection_codes(result)
    assert EvidenceRejectionCode.ROOT_CAUSE_WITHOUT_VALID_EVIDENCE in rejection_codes(result)
    assert result.rejections[0].as_storage_value() == "UNKNOWN_LINE_ID:line_id=99"


def test_modified_evidence_text_is_rejected() -> None:
    result = validate_ai_evidence(
        ai_response(evidence=[AIEvidence(line_id=2, text="ValueError: a different order")]),
        excerpt().lines,
        log_processor=LogProcessor(),
    )

    assert EvidenceRejectionCode.EVIDENCE_TEXT_MISMATCH in rejection_codes(result)
    assert EvidenceRejectionCode.ROOT_CAUSE_WITHOUT_VALID_EVIDENCE in rejection_codes(result)


def test_root_cause_without_evidence_is_rejected() -> None:
    result = validate_ai_evidence(
        ai_response(evidence=[]),
        excerpt().lines,
        log_processor=LogProcessor(),
    )

    assert result.status == DiagnosisValidationStatus.REJECTED
    assert rejection_codes(result) == {
        EvidenceRejectionCode.MISSING_EVIDENCE,
        EvidenceRejectionCode.ROOT_CAUSE_WITHOUT_VALID_EVIDENCE,
    }


def test_secret_reexposure_is_masked_and_rejected_before_persistence() -> None:
    secret = "do-not-store-this"
    response = ai_response(
        root_cause=f"password={secret} caused the failure",
        evidence=[AIEvidence(line_id=2, text=f"token={secret}")],
        recommended_actions=[f"Set api_key={secret}"],
    )

    result = validate_ai_evidence(
        response,
        excerpt().lines,
        log_processor=LogProcessor(),
    )

    assert result.status == DiagnosisValidationStatus.REJECTED
    assert EvidenceRejectionCode.SECRET_REEXPOSURE in rejection_codes(result)
    assert secret not in repr(result.response)
    assert "[REDACTED]" in repr(result.response)
