"""Stable Diagnosis contracts shared by persistence and diagnosis stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DIAGNOSIS_SCHEMA_VERSION = 3


class EvidenceBasis(StrEnum):
    DIRECT = "DIRECT"
    INFERRED = "INFERRED"
    UNDETERMINED = "UNDETERMINED"


AssessmentText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DiagnosisAssessment(BaseModel):
    """Support for the stated conclusion, not a probability of correctness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: EvidenceBasis = Field(
        description="Support for the exact stated conclusion; conflicting or insufficient evidence requires UNDETERMINED."
    )
    reason: AssessmentText
    facts: list[AssessmentText] = Field(
        description="Observed facts in Korean. DIRECT requires at least one fact supported by cited logs."
    )
    candidate_causes: list[AssessmentText]
    missing_information: list[AssessmentText] = Field(
        description="Missing non-secret information in Korean. INFERRED and UNDETERMINED require at least one item. Never request credential values. Ask only for local check results such as 자격 증명 설정 여부, 만료 시각, 권한 확인 결과."
    )
    next_checks: list[AssessmentText] = Field(
        description="Concrete local checks in Korean. INFERRED and UNDETERMINED require at least one item. Check credential configuration, expiry and permissions locally; never request passwords, token values or raw Authorization headers."
    )

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if self.basis == EvidenceBasis.DIRECT and not self.facts:
            raise ValueError("Direct conclusions require observed facts")
        if self.basis != EvidenceBasis.DIRECT and (
            not self.missing_information or not self.next_checks
        ):
            raise ValueError("Uncertain conclusions require missing information and next checks")
        return self


class ErrorClassification(StrEnum):
    """Stable top-level failure classifications shared by diagnosis stages."""

    DAG_CODE = "DAG_CODE"
    AIRFLOW_PLATFORM = "AIRFLOW_PLATFORM"
    SOURCE_DATABASE = "SOURCE_DATABASE"
    NETWORK = "NETWORK"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    RESOURCE = "RESOURCE"
    DATA_QUALITY = "DATA_QUALITY"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


class DiagnosisSource(StrEnum):
    """Producer of a stored Diagnosis."""

    RULE = "RULE"
    AI = "AI"
    REUSED = "REUSED"


class DiagnosisValidationStatus(StrEnum):
    """Whether a Diagnosis is safe for reuse and downstream decisions."""

    PASSED = "PASSED"
    REJECTED = "REJECTED"


class RetryDecision(StrEnum):
    """Advisory retry classification; it never triggers an Airflow retry."""

    RETRYABLE = "RETRYABLE"
    NOT_RETRYABLE = "NOT_RETRYABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DiagnosisEvidence:
    """One exact line from the sanitized excerpt used as evidence."""

    line_id: int
    text: str

    def __post_init__(self) -> None:
        if self.line_id < 1:
            raise ValueError("evidence line_id must be positive")
        if not self.text:
            raise ValueError("evidence text must not be empty")


@dataclass(frozen=True)
class DiagnosisExtractedValue:
    """One structured value extracted by a deterministic rule."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name or not self.value:
            raise ValueError("extracted value name and value must not be empty")


@dataclass(frozen=True)
class DiagnosisContent:
    """Materialized content stored only on original Rule or AI diagnoses."""

    classification: ErrorClassification
    confidence: float
    evidence: tuple[DiagnosisEvidence, ...]
    recommended_actions: tuple[str, ...]
    retry_decision: RetryDecision
    root_cause: str | None = None
    matched_rule: str | None = None
    confidence_reason: str | None = None
    extracted_values: tuple[DiagnosisExtractedValue, ...] = ()
    assessment: DiagnosisAssessment | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.root_cause == "":
            raise ValueError("root_cause must not be empty")
        if self.matched_rule == "":
            raise ValueError("matched_rule must not be empty")
        if self.confidence_reason == "":
            raise ValueError("confidence_reason must not be empty")
        if any(not action for action in self.recommended_actions):
            raise ValueError("recommended actions must not be empty")


@dataclass(frozen=True)
class DiagnosisVersions:
    """Versions that determine whether stored Diagnosis content is compatible."""

    schema_version: int
    prompt_version: str | None
    rule_version: int | None

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if self.prompt_version == "":
            raise ValueError("prompt_version must not be empty")
        if self.rule_version is not None and self.rule_version < 1:
            raise ValueError("rule_version must be positive")


@dataclass(frozen=True)
class DiagnosisReusePolicy:
    """Exact compatibility and freshness contract for Diagnosis reuse."""

    fingerprint_version: int
    versions: DiagnosisVersions
    max_age: timedelta

    def __post_init__(self) -> None:
        if self.fingerprint_version < 1:
            raise ValueError("fingerprint_version must be positive")
        if self.max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
