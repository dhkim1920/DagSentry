"""Validate AI Diagnosis evidence against the exact sanitized excerpt."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from dagsentry.domain.diagnosis import (
    DiagnosisValidationStatus,
    ErrorClassification,
    EvidenceBasis,
    RetryDecision,
)
from dagsentry.llm import AIDiagnosisResponse, AIEvidence
from dagsentry.log_processing import LogProcessor

_CREDENTIAL_REQUEST_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:비밀번호|패스워드|토큰|API\s*키|자격\s*증명|Authorization\s*헤더)"
        r"\s*(?:의\s*)?(?:원문|값|내용)?(?:을|를)?\s*(?:보내|전송|공유|업로드|제공|알려|제출)",
        r"(?:비밀번호|패스워드|토큰|API\s*키|자격\s*증명)(?:의)?\s*(?:원문|값)\s*$",
        r"Authorization\s*(?:헤더|header)(?:의)?\s*(?:내용|원문|값|contents?|values?)",
        r"\b(?:request|share|send|upload|provide|submit|paste)\b[^.\n]{0,80}"
        r"\b(?:passwords?|tokens?|api[ _-]*keys?|authorization[ ]*headers?|credentials?)\b"
        r"(?![ ]+(?:expiry|expiration|presence|permissions?|status|validity|redacted)\b)",
        r"\b(?:raw[ ]+)?(?:password|token|api[ _-]*key|credential)[ ]+values?\s*$",
    )
)


_CREDENTIAL_WARNINGS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"do not (?:share|send|upload|provide|submit|paste) (?:your |the )?"
        r"(?:password|token|api key|authorization header|credentials)(?: with (?:anyone|others))?",
        r"(?:비밀번호|패스워드|토큰|API\s*키|자격\s*증명|Authorization\s*헤더)"
        r"\s*(?:원문|값|내용)?(?:은|는|을|를)?\s*"
        r"(?:공유|전송|업로드|제공|제출)하지\s*(?:마세요|마십시오)",
    )
)


def _requests_credential_originals(text: str) -> bool:
    for sentence in re.split(r"[.!?;\n]", text):
        sentence = sentence.strip()
        if any(warning.fullmatch(sentence) for warning in _CREDENTIAL_WARNINGS):
            continue
        if any(pattern.search(sentence) for pattern in _CREDENTIAL_REQUEST_PATTERNS):
            return True
    return False


class EvidenceLine(Protocol):
    """Minimum exact excerpt line contract required by the Validator."""

    @property
    def line_id(self) -> int:
        """Stable one-based source line ID."""

    @property
    def text(self) -> str:
        """Exact sanitized line text."""


class EvidenceRejectionCode(StrEnum):
    """Stable reasons an AI Diagnosis cannot be trusted downstream."""

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    UNKNOWN_LINE_ID = "UNKNOWN_LINE_ID"
    EVIDENCE_TEXT_MISMATCH = "EVIDENCE_TEXT_MISMATCH"
    ROOT_CAUSE_WITHOUT_VALID_EVIDENCE = "ROOT_CAUSE_WITHOUT_VALID_EVIDENCE"
    SECRET_REEXPOSURE = "SECRET_REEXPOSURE"
    UNSAFE_CREDENTIAL_REQUEST = "UNSAFE_CREDENTIAL_REQUEST"


@dataclass(frozen=True)
class EvidenceRejection:
    """One deterministic validation failure, optionally tied to an excerpt line."""

    code: EvidenceRejectionCode
    line_id: int | None = None

    def as_storage_value(self) -> str:
        """Return a compact, non-secret value suitable for persistence."""
        if self.line_id is None:
            return self.code.value
        return f"{self.code.value}:line_id={self.line_id}"


@dataclass(frozen=True)
class AIEvidenceValidationResult:
    """Sanitized AI content and the decision controlling persistence and reuse."""

    status: DiagnosisValidationStatus
    response: AIDiagnosisResponse
    rejections: tuple[EvidenceRejection, ...]


def validate_ai_evidence(
    response: AIDiagnosisResponse,
    excerpt_lines: Iterable[EvidenceLine],
    *,
    log_processor: LogProcessor,
) -> AIEvidenceValidationResult:
    """Validate line identity/text and reject any Secret re-exposure."""
    sanitized_response, secret_changed = _sanitize_response(response, log_processor)
    rejections: list[EvidenceRejection] = []
    if secret_changed:
        rejections.append(EvidenceRejection(EvidenceRejectionCode.SECRET_REEXPOSURE))

    narratives = (
        response.root_cause,
        *response.recommended_actions,
        response.assessment.reason,
        *response.assessment.facts,
        *response.assessment.candidate_causes,
        *response.assessment.missing_information,
        *response.assessment.next_checks,
    )
    if any(_requests_credential_originals(text) for text in narratives):
        rejections.append(EvidenceRejection(EvidenceRejectionCode.UNSAFE_CREDENTIAL_REQUEST))

    excerpt_by_line_id = {line.line_id: line.text for line in excerpt_lines}
    valid_evidence_count = 0
    abstained = sanitized_response.assessment.basis == EvidenceBasis.UNDETERMINED
    if not sanitized_response.evidence and not abstained:
        rejections.append(EvidenceRejection(EvidenceRejectionCode.MISSING_EVIDENCE))
    for evidence in sanitized_response.evidence:
        expected_text = excerpt_by_line_id.get(evidence.line_id)
        if expected_text is None:
            rejections.append(
                EvidenceRejection(EvidenceRejectionCode.UNKNOWN_LINE_ID, evidence.line_id)
            )
        elif evidence.text != expected_text:
            rejections.append(
                EvidenceRejection(
                    EvidenceRejectionCode.EVIDENCE_TEXT_MISMATCH,
                    evidence.line_id,
                )
            )
        else:
            valid_evidence_count += 1

    if sanitized_response.root_cause and valid_evidence_count == 0 and not abstained:
        rejections.append(
            EvidenceRejection(EvidenceRejectionCode.ROOT_CAUSE_WITHOUT_VALID_EVIDENCE)
        )

    return AIEvidenceValidationResult(
        status=(
            DiagnosisValidationStatus.REJECTED if rejections else DiagnosisValidationStatus.PASSED
        ),
        response=sanitized_response,
        rejections=tuple(rejections),
    )


def _sanitize_response(
    response: AIDiagnosisResponse,
    log_processor: LogProcessor,
) -> tuple[AIDiagnosisResponse, bool]:
    root_cause = log_processor.mask_secrets(response.root_cause)
    evidence = [
        AIEvidence(line_id=item.line_id, text=log_processor.mask_secrets(item.text))
        for item in response.evidence
    ]
    recommended_actions = [
        log_processor.mask_secrets(action) for action in response.recommended_actions
    ]
    assessment = response.assessment.model_copy(
        update={
            "reason": log_processor.mask_secrets(response.assessment.reason),
            **{
                name: [
                    log_processor.mask_secrets(item) for item in getattr(response.assessment, name)
                ]
                for name in ("facts", "candidate_causes", "missing_information", "next_checks")
            },
        }
    )
    sanitized = response.model_copy(
        update={
            "assessment": assessment,
            "retry_decision": (
                RetryDecision.UNKNOWN
                if assessment.basis == EvidenceBasis.UNDETERMINED
                else response.retry_decision
            ),
            "operator_review_required": response.operator_review_required
            or assessment.basis != EvidenceBasis.DIRECT,
            "root_cause": (
                "제공된 근거만으로 실패 원인을 판단할 수 없습니다."
                if assessment.basis == EvidenceBasis.UNDETERMINED
                else root_cause
            ),
            "classification": (
                ErrorClassification.UNKNOWN
                if assessment.basis == EvidenceBasis.UNDETERMINED
                else response.classification
            ),
            "evidence": evidence,
            "recommended_actions": recommended_actions,
        }
    )
    return sanitized, (
        root_cause != response.root_cause
        or evidence != response.evidence
        or recommended_actions != response.recommended_actions
        or assessment != response.assessment
    )
