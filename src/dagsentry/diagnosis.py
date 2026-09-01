"""Diagnosis persistence and version-aware reuse."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from dagsentry.domain.diagnosis import (
    DIAGNOSIS_SCHEMA_VERSION,
    DiagnosisContent,
    DiagnosisEvidence,
    DiagnosisExtractedValue,
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
    RetryDecision,
)
from dagsentry.evidence_validation import AIEvidenceValidationResult
from dagsentry.llm import LLMResult
from dagsentry.log_processing import RelevantLogExcerpt
from dagsentry.models import DiagnosisRecord, ErrorSignatureRecord
from dagsentry.rule_diagnosis import RuleDiagnosisResult


@dataclass(frozen=True)
class DiagnosisDraft:
    """A complete original Diagnosis ready for persistence."""

    failure_event_id: UUID
    error_signature_id: UUID | None
    source: DiagnosisSource
    validation_status: DiagnosisValidationStatus
    versions: DiagnosisVersions
    content: DiagnosisContent
    operator_review_required: bool | None = None
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source == DiagnosisSource.REUSED:
            raise ValueError("REUSED diagnoses must be created through reuse_diagnosis")


@dataclass(frozen=True)
class ReusableDiagnosis:
    """Compatible original Diagnosis selected without materializing its content."""

    diagnosis_id: UUID
    error_signature_id: UUID
    source: DiagnosisSource
    versions: DiagnosisVersions


@dataclass(frozen=True)
class DiagnosisReuseResult:
    """A lightweight REUSED Diagnosis and the original it references."""

    diagnosis_id: UUID
    reused_from_diagnosis_id: UUID


@dataclass(frozen=True)
class AIDiagnosisPersistResult:
    """Stored AI attempt and optional Rule fallback created in one transaction."""

    ai_diagnosis_id: UUID
    rule_fallback_diagnosis_id: UUID | None


def persist_diagnosis(session: Session, draft: DiagnosisDraft) -> UUID:
    """Persist one original Rule or AI Diagnosis with its complete provenance."""
    record = _record_from_draft(draft)
    with session.begin():
        session.add(record)
        session.flush()
    return record.id


def persist_rule_diagnosis(
    session: Session,
    *,
    failure_event_id: UUID,
    error_signature_id: UUID | None,
    result: RuleDiagnosisResult,
    excerpt: RelevantLogExcerpt | None,
) -> UUID:
    """Persist a deterministic Rule Diagnosis and its exact sanitized evidence."""
    return persist_diagnosis(
        session,
        _rule_diagnosis_draft(
            failure_event_id=failure_event_id,
            error_signature_id=error_signature_id,
            result=result,
            excerpt=excerpt,
        ),
    )


def persist_ai_validation(
    session: Session,
    *,
    failure_event_id: UUID,
    error_signature_id: UUID | None,
    ai_result: LLMResult,
    validation: AIEvidenceValidationResult,
    rule_diagnosis: RuleDiagnosisResult,
    excerpt: RelevantLogExcerpt,
) -> AIDiagnosisPersistResult:
    """Atomically store the AI decision and its Rule fallback when rejected."""
    response = validation.response
    ai_draft = DiagnosisDraft(
        failure_event_id=failure_event_id,
        error_signature_id=error_signature_id,
        source=DiagnosisSource.AI,
        validation_status=validation.status,
        versions=DiagnosisVersions(
            schema_version=DIAGNOSIS_SCHEMA_VERSION,
            prompt_version=ai_result.metadata.prompt_version,
            rule_version=rule_diagnosis.ruleset_version,
        ),
        content=DiagnosisContent(
            classification=response.classification,
            root_cause=response.root_cause,
            confidence=response.confidence,
            evidence=tuple(
                DiagnosisEvidence(line_id=item.line_id, text=item.text)
                for item in response.evidence
            ),
            recommended_actions=tuple(response.recommended_actions),
            retry_decision=response.retry_decision,
        ),
        operator_review_required=response.operator_review_required,
        validation_errors=tuple(
            rejection.as_storage_value() for rejection in validation.rejections
        ),
    )
    ai_record = _record_from_draft(ai_draft)
    rule_record: DiagnosisRecord | None = None
    if validation.status == DiagnosisValidationStatus.REJECTED:
        rule_record = _record_from_draft(
            _rule_diagnosis_draft(
                failure_event_id=failure_event_id,
                error_signature_id=error_signature_id,
                result=rule_diagnosis,
                excerpt=excerpt,
            )
        )

    with session.begin():
        session.add(ai_record)
        if rule_record is not None:
            session.add(rule_record)
        session.flush()
    return AIDiagnosisPersistResult(
        ai_diagnosis_id=ai_record.id,
        rule_fallback_diagnosis_id=rule_record.id if rule_record is not None else None,
    )


def _rule_diagnosis_draft(
    *,
    failure_event_id: UUID,
    error_signature_id: UUID | None,
    result: RuleDiagnosisResult,
    excerpt: RelevantLogExcerpt | None,
) -> DiagnosisDraft:
    excerpt_by_line_id = (
        {line.line_id: line.text for line in excerpt.lines} if excerpt is not None else {}
    )
    evidence = tuple(
        DiagnosisEvidence(line_id=line_id, text=excerpt_by_line_id[line_id])
        for line_id in result.evidence_line_ids
        if line_id in excerpt_by_line_id
    )
    return DiagnosisDraft(
        failure_event_id=failure_event_id,
        error_signature_id=error_signature_id,
        source=DiagnosisSource.RULE,
        validation_status=DiagnosisValidationStatus.PASSED,
        versions=DiagnosisVersions(
            schema_version=DIAGNOSIS_SCHEMA_VERSION,
            prompt_version=None,
            rule_version=result.ruleset_version,
        ),
        content=DiagnosisContent(
            classification=result.classification,
            confidence=result.confidence,
            confidence_reason=result.confidence_reason,
            matched_rule=result.matched_rule,
            extracted_values=tuple(
                DiagnosisExtractedValue(name=value.name, value=value.value)
                for value in result.extracted_values
            ),
            evidence=evidence,
            recommended_actions=(),
            retry_decision=RetryDecision.UNKNOWN,
        ),
    )


def find_reusable_diagnosis(
    session: Session,
    *,
    error_signature_id: UUID | None,
    policy: DiagnosisReusePolicy,
    now: datetime | None = None,
) -> ReusableDiagnosis | None:
    """Find the newest compatible, validated original for an Error Signature."""
    if error_signature_id is None:
        return None
    record = session.scalar(
        _reusable_statement(
            error_signature_id=error_signature_id,
            policy=policy,
            now=now or datetime.now(UTC),
        ).limit(1)
    )
    return _to_reusable(record) if record is not None else None


def reuse_diagnosis(
    session: Session,
    *,
    failure_event_id: UUID,
    error_signature_id: UUID | None,
    policy: DiagnosisReusePolicy,
    now: datetime | None = None,
) -> DiagnosisReuseResult | None:
    """Store a reference-only REUSED Diagnosis when a compatible original exists."""
    if error_signature_id is None:
        return None

    with session.begin():
        original = session.scalar(
            _reusable_statement(
                error_signature_id=error_signature_id,
                policy=policy,
                now=now or datetime.now(UTC),
            ).limit(1)
        )
        if original is None:
            return None
        reused = DiagnosisRecord(
            failure_event_id=failure_event_id,
            error_signature_id=error_signature_id,
            source=DiagnosisSource.REUSED,
            validation_status=DiagnosisValidationStatus.PASSED,
            classification=None,
            root_cause=None,
            confidence=None,
            confidence_reason=None,
            matched_rule=None,
            extracted_values=None,
            evidence=None,
            recommended_actions=None,
            retry_decision=None,
            operator_review_required=None,
            validation_errors=None,
            diagnosis_schema_version=original.diagnosis_schema_version,
            prompt_version=original.prompt_version,
            rule_version=original.rule_version,
            reused_from_diagnosis_id=original.id,
        )
        session.add(reused)
        session.flush()
        return DiagnosisReuseResult(
            diagnosis_id=reused.id,
            reused_from_diagnosis_id=original.id,
        )


def _reusable_statement(
    *,
    error_signature_id: UUID,
    policy: DiagnosisReusePolicy,
    now: datetime,
) -> Select[tuple[DiagnosisRecord]]:
    versions = policy.versions
    return (
        select(DiagnosisRecord)
        .join(
            ErrorSignatureRecord,
            DiagnosisRecord.error_signature_id == ErrorSignatureRecord.id,
        )
        .where(
            DiagnosisRecord.error_signature_id == error_signature_id,
            ErrorSignatureRecord.fingerprint_version == policy.fingerprint_version,
            DiagnosisRecord.source.in_((DiagnosisSource.RULE, DiagnosisSource.AI)),
            DiagnosisRecord.validation_status == DiagnosisValidationStatus.PASSED,
            DiagnosisRecord.diagnosis_schema_version == versions.schema_version,
            DiagnosisRecord.prompt_version == versions.prompt_version,
            DiagnosisRecord.rule_version == versions.rule_version,
            DiagnosisRecord.created_at >= now - policy.max_age,
        )
        .order_by(DiagnosisRecord.created_at.desc(), DiagnosisRecord.id.desc())
    )


def _to_reusable(record: DiagnosisRecord) -> ReusableDiagnosis:
    error_signature_id = record.error_signature_id
    assert error_signature_id is not None
    return ReusableDiagnosis(
        diagnosis_id=record.id,
        error_signature_id=error_signature_id,
        source=record.source,
        versions=DiagnosisVersions(
            schema_version=record.diagnosis_schema_version,
            prompt_version=record.prompt_version,
            rule_version=record.rule_version,
        ),
    )


def _record_from_draft(draft: DiagnosisDraft) -> DiagnosisRecord:
    content = draft.content
    return DiagnosisRecord(
        failure_event_id=draft.failure_event_id,
        error_signature_id=draft.error_signature_id,
        source=draft.source,
        validation_status=draft.validation_status,
        classification=content.classification,
        root_cause=content.root_cause,
        confidence=content.confidence,
        confidence_reason=content.confidence_reason,
        matched_rule=content.matched_rule,
        extracted_values=[asdict(value) for value in content.extracted_values],
        evidence=[asdict(item) for item in content.evidence],
        recommended_actions=list(content.recommended_actions),
        retry_decision=content.retry_decision,
        operator_review_required=draft.operator_review_required,
        validation_errors=list(draft.validation_errors),
        diagnosis_schema_version=draft.versions.schema_version,
        prompt_version=draft.versions.prompt_version,
        rule_version=draft.versions.rule_version,
        reused_from_diagnosis_id=None,
    )
