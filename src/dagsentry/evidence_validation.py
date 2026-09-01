"""Validate AI Diagnosis evidence against the exact sanitized excerpt."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from dagsentry.domain.diagnosis import DiagnosisValidationStatus
from dagsentry.llm import AIDiagnosisResponse, AIEvidence
from dagsentry.log_processing import LogProcessor


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

    excerpt_by_line_id = {line.line_id: line.text for line in excerpt_lines}
    valid_evidence_count = 0
    if not sanitized_response.evidence:
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

    if sanitized_response.root_cause and valid_evidence_count == 0:
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
    sanitized = response.model_copy(
        update={
            "root_cause": root_cause,
            "evidence": evidence,
            "recommended_actions": recommended_actions,
        }
    )
    return sanitized, sanitized != response
