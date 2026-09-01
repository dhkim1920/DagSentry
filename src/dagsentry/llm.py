"""Provider-neutral contracts for bounded AI Diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from dagsentry.domain.diagnosis import ErrorClassification, RetryDecision


class AIEvidence(BaseModel):
    """One evidence line claimed by an LLM response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: int = Field(gt=0)
    text: str = Field(min_length=1)


@dataclass(frozen=True)
class ExcerptLineContext:
    """One sanitized relevant excerpt line allowed in an AI request."""

    line_id: int
    text: str

    def __post_init__(self) -> None:
        if self.line_id < 1 or not self.text:
            raise ValueError("excerpt line ID and text must be present")


class AIDiagnosisResponse(BaseModel):
    """Strict structured output requested from every LLM Provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: ErrorClassification
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[AIEvidence]
    recommended_actions: list[str] = Field(min_length=1)
    retry_decision: RetryDecision
    operator_review_required: bool


@dataclass(frozen=True)
class FailureMetadata:
    """Bounded Failure Event metadata allowed in an AI request."""

    environment: str
    dag_id: str
    dag_run_id: str
    task_id: str
    map_index: int
    try_number: int
    state: str
    observed_at: datetime
    operator_type: str | None


@dataclass(frozen=True)
class RuleDiagnosisContext:
    """Deterministic diagnosis context supplied to AI."""

    classification: ErrorClassification
    matched_rule: str
    ruleset_version: int
    confidence: float
    confidence_reason: str
    extracted_values: tuple[dict[str, str], ...]
    evidence_line_ids: tuple[int, ...]


@dataclass(frozen=True)
class ErrorSignatureContext:
    """Versioned Error Signature context supplied to AI."""

    fingerprint: str
    fingerprint_version: int
    operator_type: str | None
    exception_class: str | None
    vendor_error_code: str | None
    normalized_message: str | None
    application_stack_frame: str | None


@dataclass(frozen=True)
class ValidatedPriorDiagnosis:
    """Only a previously PASSED Diagnosis may be represented by this type."""

    classification: ErrorClassification
    root_cause: str | None
    confidence: float
    evidence: tuple[ExcerptLineContext, ...]
    recommended_actions: tuple[str, ...]
    retry_decision: RetryDecision
    schema_version: int
    prompt_version: str | None
    rule_version: int | None


@dataclass(frozen=True)
class AIDiagnosisRequest:
    """Complete allowlist of data that can cross an LLM Provider boundary."""

    metadata: FailureMetadata
    rule_diagnosis: RuleDiagnosisContext
    error_signature: ErrorSignatureContext | None
    excerpt: tuple[ExcerptLineContext, ...]
    prior_diagnosis: ValidatedPriorDiagnosis | None = None

    def to_json_value(self) -> dict[str, object]:
        """Serialize only the explicitly allowed request fields."""
        value = asdict(self)
        metadata = value["metadata"]
        assert isinstance(metadata, dict)
        metadata["observed_at"] = self.metadata.observed_at.isoformat()
        return value


@dataclass(frozen=True)
class LLMCallMetadata:
    """Non-secret provider call telemetry returned to the Core."""

    request_id: str | None
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class LLMResult:
    """Provider-neutral structured response and call metadata."""

    diagnosis: AIDiagnosisResponse
    metadata: LLMCallMetadata


class LLMProviderError(RuntimeError):
    """A sanitized Provider failure safe for fallback handling and logs."""


class LLMProvider(Protocol):
    """Minimal Provider boundary consumed by AI Diagnosis Core."""

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        """Return one schema-conforming diagnosis or raise LLMProviderError."""
