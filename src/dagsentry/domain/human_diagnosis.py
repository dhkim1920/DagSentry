"""Immutable operator-authored Incident diagnosis contracts."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision


class HumanDiagnosisAction(StrEnum):
    """One append-only operation in an Incident's human diagnosis history."""

    PUBLISH = "PUBLISH"
    WITHDRAW = "WITHDRAW"


@dataclass(frozen=True)
class HumanDiagnosisEvidenceReference:
    """A selected sanitized Evidence line from a stored Diagnosis."""

    source_diagnosis_id: UUID
    line_id: int


@dataclass(frozen=True)
class HumanDiagnosisPublish:
    """Validated operator content before persistence and server-side masking checks."""

    expected_revision: int
    basis_diagnosis_id: UUID | None
    classification: ErrorClassification
    root_cause: str
    recommended_actions: tuple[str, ...]
    retry_decision: RetryDecision
    evidence: tuple[HumanDiagnosisEvidenceReference, ...]
    operator_notes: str | None
    change_reason: str | None


@dataclass(frozen=True)
class HumanDiagnosisWithdraw:
    """One append-only withdrawal request."""

    expected_revision: int
    reason: str
