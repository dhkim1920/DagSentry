from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from dagsentry.diagnosis import (
    DiagnosisDraft,
    find_reusable_diagnosis,
    persist_ai_validation,
    persist_diagnosis,
    persist_rule_diagnosis,
    reuse_diagnosis,
)
from dagsentry.domain.diagnosis import (
    DIAGNOSIS_SCHEMA_VERSION,
    DiagnosisContent,
    DiagnosisEvidence,
    DiagnosisReusePolicy,
    DiagnosisSource,
    DiagnosisValidationStatus,
    DiagnosisVersions,
    ErrorClassification,
    RetryDecision,
)
from dagsentry.domain.failure_event import CollectionSource, FailureState
from dagsentry.error_signature import (
    FINGERPRINT_VERSION,
    ErrorSignatureInput,
    build_error_signature,
    persist_error_signature,
)
from dagsentry.evidence_validation import validate_ai_evidence
from dagsentry.llm import (
    AIDiagnosisResponse,
    AIEvidence,
    LLMCallMetadata,
    LLMResult,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.models import DiagnosisRecord, FailureEventRecord
from dagsentry.rule_diagnosis import RuleDiagnosisInput, RuleDiagnosisResult, RuleEngine

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def create_failure(session: Session, *, try_number: int) -> UUID:
    record = FailureEventRecord(
        event_key=f"{try_number:064x}",
        event_key_version=1,
        environment="test",
        dag_id="orders",
        dag_run_id="scheduled__2026-08-10",
        task_id="load",
        map_index=-1,
        try_number=try_number,
        source=CollectionSource.LISTENER,
        state=FailureState.FAILED,
        observed_at=NOW,
    )
    with session.begin():
        session.add(record)
        session.flush()
    return record.id


def create_signature(session: Session) -> UUID:
    candidate = build_error_signature(
        ErrorSignatureInput(
            operator_type="PythonOperator",
            exception_class="ValueError",
            vendor_error_code=None,
            normalized_message="ValueError: invalid order",
            application_stack_frame=None,
        )
    )
    result = persist_error_signature(session, candidate)
    assert result.signature_id is not None
    return result.signature_id


def versions(
    *, schema: int = 1, prompt: str | None = "ai-v1", rule: int | None = 1
) -> DiagnosisVersions:
    return DiagnosisVersions(schema_version=schema, prompt_version=prompt, rule_version=rule)


def policy(
    *,
    fingerprint: int = FINGERPRINT_VERSION,
    schema: int = 1,
    prompt: str | None = "ai-v1",
    rule: int | None = 1,
    max_age: timedelta = timedelta(days=30),
) -> DiagnosisReusePolicy:
    return DiagnosisReusePolicy(
        fingerprint_version=fingerprint,
        versions=versions(schema=schema, prompt=prompt, rule=rule),
        max_age=max_age,
    )


def ai_draft(
    failure_event_id: UUID,
    signature_id: UUID,
    *,
    validation_status: DiagnosisValidationStatus = DiagnosisValidationStatus.PASSED,
) -> DiagnosisDraft:
    return DiagnosisDraft(
        failure_event_id=failure_event_id,
        error_signature_id=signature_id,
        source=DiagnosisSource.AI,
        validation_status=validation_status,
        versions=versions(),
        content=DiagnosisContent(
            classification=ErrorClassification.DAG_CODE,
            root_cause="The task rejected an invalid order.",
            confidence=0.91,
            confidence_reason="The exception and message agree.",
            evidence=(DiagnosisEvidence(line_id=7, text="ValueError: invalid order"),),
            recommended_actions=("Validate the order before loading it.",),
            retry_decision=RetryDecision.NOT_RETRYABLE,
        ),
    )


def test_persists_ai_diagnosis_content_and_versions(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)

    diagnosis_id = persist_diagnosis(session, ai_draft(failure_id, signature_id))

    record = session.get(DiagnosisRecord, diagnosis_id)
    assert record is not None
    assert record.source == DiagnosisSource.AI
    assert record.validation_status == DiagnosisValidationStatus.PASSED
    assert record.classification == ErrorClassification.DAG_CODE
    assert record.root_cause == "The task rejected an invalid order."
    assert record.confidence == 0.91
    assert record.evidence == [{"line_id": 7, "text": "ValueError: invalid order"}]
    assert record.recommended_actions == ["Validate the order before loading it."]
    assert record.retry_decision == RetryDecision.NOT_RETRYABLE
    assert record.diagnosis_schema_version == DIAGNOSIS_SCHEMA_VERSION
    assert record.prompt_version == "ai-v1"
    assert record.rule_version == 1


def test_persists_rule_result_with_evidence_and_extracted_values(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    excerpt = LogProcessor().process("HTTP 401 Unauthorized")
    result = RuleEngine().diagnose(RuleDiagnosisInput("HttpOperator", excerpt))

    diagnosis_id = persist_rule_diagnosis(
        session,
        failure_event_id=failure_id,
        error_signature_id=signature_id,
        result=result,
        excerpt=excerpt,
    )

    record = session.get(DiagnosisRecord, diagnosis_id)
    assert record is not None
    assert record.source == DiagnosisSource.RULE
    assert record.validation_status == DiagnosisValidationStatus.PASSED
    assert record.matched_rule == "http.authentication_401.v1"
    assert record.confidence_reason == (
        "Explicit HTTP 401 status was found in sanitized log evidence"
    )
    assert record.extracted_values == [{"name": "http_status", "value": "401"}]
    assert record.evidence == [{"line_id": 1, "text": "HTTP 401 Unauthorized"}]
    assert record.prompt_version is None
    assert record.rule_version == 1


def test_validated_compatible_diagnosis_is_reused_without_ai_call(session: Session) -> None:
    original_failure_id = create_failure(session, try_number=1)
    repeated_failure_id = create_failure(session, try_number=2)
    signature_id = create_signature(session)
    original_id = persist_diagnosis(
        session,
        ai_draft(original_failure_id, signature_id),
    )
    ai_calls = 0

    reused = reuse_diagnosis(
        session,
        failure_event_id=repeated_failure_id,
        error_signature_id=signature_id,
        policy=policy(),
        now=NOW,
    )
    if reused is None:
        ai_calls += 1

    assert ai_calls == 0
    assert reused is not None
    assert reused.reused_from_diagnosis_id == original_id
    record = session.get(DiagnosisRecord, reused.diagnosis_id)
    assert record is not None
    assert record.source == DiagnosisSource.REUSED
    assert record.reused_from_diagnosis_id == original_id
    assert record.classification is None
    assert record.root_cause is None
    assert record.evidence is None
    assert record.recommended_actions is None


def test_reuse_always_points_to_original_instead_of_chaining(session: Session) -> None:
    original_failure_id = create_failure(session, try_number=1)
    first_repeat_id = create_failure(session, try_number=2)
    second_repeat_id = create_failure(session, try_number=3)
    signature_id = create_signature(session)
    original_id = persist_diagnosis(session, ai_draft(original_failure_id, signature_id))
    first_reuse = reuse_diagnosis(
        session,
        failure_event_id=first_repeat_id,
        error_signature_id=signature_id,
        policy=policy(),
        now=NOW,
    )
    assert first_reuse is not None

    second_reuse = reuse_diagnosis(
        session,
        failure_event_id=second_repeat_id,
        error_signature_id=signature_id,
        policy=policy(),
        now=NOW,
    )

    assert second_reuse is not None
    assert second_reuse.reused_from_diagnosis_id == original_id
    assert second_reuse.reused_from_diagnosis_id != first_reuse.diagnosis_id


def test_rejected_diagnosis_is_not_reusable(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    persist_diagnosis(
        session,
        ai_draft(
            failure_id,
            signature_id,
            validation_status=DiagnosisValidationStatus.REJECTED,
        ),
    )

    assert (
        find_reusable_diagnosis(
            session,
            error_signature_id=signature_id,
            policy=policy(),
            now=NOW,
        )
        is None
    )


def test_stale_diagnosis_is_not_reusable(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    diagnosis_id = persist_diagnosis(session, ai_draft(failure_id, signature_id))
    with session.begin():
        record = session.get(DiagnosisRecord, diagnosis_id)
        assert record is not None
        record.created_at = NOW - timedelta(days=31)

    assert (
        find_reusable_diagnosis(
            session,
            error_signature_id=signature_id,
            policy=policy(max_age=timedelta(days=30)),
            now=NOW,
        )
        is None
    )


@pytest.mark.parametrize(
    "incompatible_policy",
    [
        policy(fingerprint=2),
        policy(schema=2),
        policy(prompt="ai-v2"),
        policy(rule=2),
    ],
    ids=["fingerprint", "schema", "prompt", "rule"],
)
def test_incompatible_versions_are_not_reusable(
    session: Session,
    incompatible_policy: DiagnosisReusePolicy,
) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    persist_diagnosis(session, ai_draft(failure_id, signature_id))

    assert (
        find_reusable_diagnosis(
            session,
            error_signature_id=signature_id,
            policy=incompatible_policy,
            now=NOW,
        )
        is None
    )


def test_unsignable_diagnosis_is_not_reusable(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    draft = ai_draft(failure_id, create_signature(session))
    persist_diagnosis(
        session,
        DiagnosisDraft(
            failure_event_id=draft.failure_event_id,
            error_signature_id=None,
            source=draft.source,
            validation_status=draft.validation_status,
            versions=draft.versions,
            content=draft.content,
        ),
    )

    assert (
        find_reusable_diagnosis(
            session,
            error_signature_id=None,
            policy=policy(),
            now=NOW,
        )
        is None
    )


def test_reuse_query_selects_newest_compatible_original(session: Session) -> None:
    first_failure_id = create_failure(session, try_number=1)
    second_failure_id = create_failure(session, try_number=2)
    signature_id = create_signature(session)
    first_id = persist_diagnosis(session, ai_draft(first_failure_id, signature_id))
    second_id = persist_diagnosis(session, ai_draft(second_failure_id, signature_id))
    with session.begin():
        records = session.scalars(
            select(DiagnosisRecord).where(DiagnosisRecord.id.in_((first_id, second_id)))
        ).all()
        by_id = {record.id: record for record in records}
        by_id[first_id].created_at = NOW - timedelta(days=2)
        by_id[second_id].created_at = NOW - timedelta(days=1)

    reusable = find_reusable_diagnosis(
        session,
        error_signature_id=signature_id,
        policy=policy(),
        now=NOW,
    )

    assert reusable is not None
    assert reusable.diagnosis_id == second_id


def llm_result(*, evidence_line_id: int = 1, root_cause: str = "Invalid order") -> LLMResult:
    return LLMResult(
        diagnosis=AIDiagnosisResponse(
            classification=ErrorClassification.DAG_CODE,
            root_cause=root_cause,
            confidence=0.9,
            evidence=[AIEvidence(line_id=evidence_line_id, text="ValueError: invalid order")],
            recommended_actions=["Validate the order input"],
            retry_decision=RetryDecision.NOT_RETRYABLE,
            operator_review_required=True,
        ),
        metadata=LLMCallMetadata(
            request_id="req_1",
            model="test-model",
            prompt_version="ai-v1",
            latency_ms=12,
            input_tokens=20,
            output_tokens=10,
        ),
    )


def fallback_rule() -> RuleDiagnosisResult:
    return RuleDiagnosisResult(
        classification=ErrorClassification.DAG_CODE,
        matched_rule="python.application_exception.v1",
        ruleset_version=1,
        confidence=0.85,
        confidence_reason="Application exception",
        extracted_values=(),
        evidence_line_ids=(1,),
    )


def test_passed_ai_validation_persists_without_rule_fallback(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    excerpt = LogProcessor().process("ValueError: invalid order")
    ai = llm_result()
    validation = validate_ai_evidence(ai.diagnosis, excerpt.lines, log_processor=LogProcessor())

    persisted = persist_ai_validation(
        session,
        failure_event_id=failure_id,
        error_signature_id=signature_id,
        ai_result=ai,
        validation=validation,
        rule_diagnosis=fallback_rule(),
        excerpt=excerpt,
    )

    assert persisted.rule_fallback_diagnosis_id is None
    record = session.get(DiagnosisRecord, persisted.ai_diagnosis_id)
    assert record is not None
    assert record.validation_status == DiagnosisValidationStatus.PASSED
    assert record.operator_review_required is True
    assert record.validation_errors == []


def test_rejected_ai_validation_and_rule_fallback_are_stored_together(
    session: Session,
) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    excerpt = LogProcessor().process("ValueError: invalid order")
    ai = llm_result(evidence_line_id=99)
    validation = validate_ai_evidence(ai.diagnosis, excerpt.lines, log_processor=LogProcessor())

    persisted = persist_ai_validation(
        session,
        failure_event_id=failure_id,
        error_signature_id=signature_id,
        ai_result=ai,
        validation=validation,
        rule_diagnosis=fallback_rule(),
        excerpt=excerpt,
    )

    assert persisted.rule_fallback_diagnosis_id is not None
    records = session.scalars(
        select(DiagnosisRecord)
        .where(DiagnosisRecord.failure_event_id == failure_id)
        .order_by(DiagnosisRecord.source)
    ).all()
    assert len(records) == 2
    by_source = {record.source: record for record in records}
    rejected = by_source[DiagnosisSource.AI]
    fallback = by_source[DiagnosisSource.RULE]
    assert rejected.validation_status == DiagnosisValidationStatus.REJECTED
    assert rejected.validation_errors == [
        "UNKNOWN_LINE_ID:line_id=99",
        "ROOT_CAUSE_WITHOUT_VALID_EVIDENCE",
    ]
    assert fallback.validation_status == DiagnosisValidationStatus.PASSED
    assert fallback.prompt_version is None


def test_secret_is_masked_in_rejected_ai_record(session: Session) -> None:
    failure_id = create_failure(session, try_number=1)
    signature_id = create_signature(session)
    excerpt = LogProcessor().process("ValueError: invalid order")
    secret = "do-not-store-this"
    ai = llm_result(root_cause=f"password={secret} caused the failure")
    validation = validate_ai_evidence(ai.diagnosis, excerpt.lines, log_processor=LogProcessor())

    persisted = persist_ai_validation(
        session,
        failure_event_id=failure_id,
        error_signature_id=signature_id,
        ai_result=ai,
        validation=validation,
        rule_diagnosis=fallback_rule(),
        excerpt=excerpt,
    )

    record = session.get(DiagnosisRecord, persisted.ai_diagnosis_id)
    assert record is not None
    assert record.validation_status == DiagnosisValidationStatus.REJECTED
    assert record.root_cause is not None
    assert secret not in record.root_cause
    assert "[REDACTED]" in record.root_cause
