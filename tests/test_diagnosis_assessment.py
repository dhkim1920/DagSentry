from __future__ import annotations

import pytest
from pydantic import ValidationError

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from dagsentry.domain.diagnosis import DiagnosisAssessment, EvidenceBasis, RetryDecision
from dagsentry.evidence_validation import validate_ai_evidence
from dagsentry.llm import AIDiagnosisResponse, AIEvidence
from dagsentry.log_processing import LogProcessor
from dagsentry.models import DiagnosisRecord
from dagsentry.rule_diagnosis import RuleDiagnosisInput, RuleEngine, assess_rule_diagnosis
from tests.test_diagnosis_api import create_diagnosis_history
from tests.test_evidence_validation import ai_response, excerpt
from tests.test_incident_api import TOKEN_HEADER, request


def uncertain() -> DiagnosisAssessment:
    return DiagnosisAssessment(
        basis=EvidenceBasis.UNDETERMINED,
        reason="정보가 부족하거나 충돌하여 원인을 판단할 수 없습니다.",
        facts=[],
        candidate_causes=[],
        missing_information=["실패 시점 로그"],
        next_checks=["실패 시점 로그를 확인하세요."],
    )


def test_new_ai_schema_requires_assessment() -> None:
    value = ai_response().model_dump()
    del value["assessment"]
    with pytest.raises(ValidationError):
        AIDiagnosisResponse.model_validate(value)


def test_no_evidence_abstention_is_valid_and_requires_review() -> None:
    response = ai_response(evidence=[]).model_copy(
        update={"assessment": uncertain(), "operator_review_required": False}
    )
    result = validate_ai_evidence(response, [], log_processor=LogProcessor())
    assert result.status == "PASSED"
    assert result.response.assessment.basis == "UNDETERMINED"
    assert result.response.operator_review_required


def test_abstention_does_not_excuse_invented_citations() -> None:
    response = ai_response(evidence=[AIEvidence(line_id=88, text="invented")]).model_copy(
        update={"assessment": uncertain()}
    )
    result = validate_ai_evidence(response, excerpt().lines, log_processor=LogProcessor())
    assert result.status == "REJECTED"
    assert result.rejections[0].code == "UNKNOWN_LINE_ID"


@pytest.mark.parametrize(
    "field", ["reason", "facts", "candidate_causes", "missing_information", "next_checks"]
)
def test_assessment_secrets_are_masked_and_rejected(field: str) -> None:
    value = "password=assessment-secret"
    assessment = uncertain().model_copy(update={field: value if field == "reason" else [value]})
    response = ai_response().model_copy(update={"assessment": assessment})
    result = validate_ai_evidence(response, excerpt().lines, log_processor=LogProcessor())
    assert result.status == "REJECTED"
    assert "assessment-secret" not in result.response.model_dump_json()
    assert any(item.code == "SECRET_REEXPOSURE" for item in result.rejections)


@pytest.mark.parametrize(
    "log,expected,forbidden",
    [
        ("HTTP 401 Unauthorized", "HTTP 401", "토큰 만료"),
        ("MemoryError", "메모리 부족 신호", "메모리 누수"),
        ("OOMKilled", "메모리 부족 신호", "메모리 누수"),
        ("ORA-12541: TNS:no listener", "ORA-12541", "중지됐"),
    ],
)
def test_rule_facts_do_not_overclaim_root_cause(log: str, expected: str, forbidden: str) -> None:
    result = RuleEngine().diagnose(RuleDiagnosisInput(None, LogProcessor().process(log)))
    assessment = assess_rule_diagnosis(result, has_evidence=True)
    assert assessment.basis == "DIRECT"
    assert expected in assessment.facts[0]
    assert forbidden not in " ".join(assessment.facts)
    assert assessment.missing_information and assessment.next_checks


@pytest.mark.parametrize("log", [None, "Connection timed out", "Task failed"])
def test_unknown_rule_cannot_claim_direct_support(log: str | None) -> None:
    excerpt_value = LogProcessor().process(log) if log else None
    result = RuleEngine().diagnose(RuleDiagnosisInput(None, excerpt_value))
    assessment = assess_rule_diagnosis(result, has_evidence=bool(result.evidence_line_ids))
    assert assessment.basis == "UNDETERMINED"
    assert not assessment.facts


def test_assessment_survives_storage_api_and_reuse_without_backfilling_legacy(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    incident_id, _, signature_id, rejected_id, effective_id, reused_id = create_diagnosis_history(
        session_factory
    )
    app = create_app(settings, session_factory)
    assert (
        request(app, "GET", f"/api/v1/diagnoses/{reused_id}", headers=TOKEN_HEADER).json()[
            "assessment"
        ]
        is None
    )
    with session_factory() as session:
        record = session.get(DiagnosisRecord, effective_id)
        assert record is not None
        record.assessment = uncertain().model_dump(mode="json")
        session.commit()
    for diagnosis_id in [effective_id, reused_id]:
        response = request(app, "GET", f"/api/v1/diagnoses/{diagnosis_id}", headers=TOKEN_HEADER)
        assert response.status_code == 200
        assert response.json()["assessment"] == uncertain().model_dump(mode="json")
    legacy = request(app, "GET", f"/api/v1/diagnoses/{rejected_id}", headers=TOKEN_HEADER)
    assert legacy.json()["assessment"] is None
    listing = request(app, "GET", "/api/v1/diagnoses", headers=TOKEN_HEADER).json()
    assert (
        next(row for row in listing["items"] if row["id"] == str(reused_id))["assessment"]["basis"]
        == "UNDETERMINED"
    )
    incident = request(app, "GET", f"/api/v1/incidents/{incident_id}", headers=TOKEN_HEADER)
    assert incident.status_code == 200
    assert '"assessment":{"basis":"UNDETERMINED"' in incident.text
    signature = request(
        app, "GET", f"/api/v1/error-signatures/{signature_id}", headers=TOKEN_HEADER
    )
    assert signature.status_code == 200
    assert '"assessment":{"basis":"UNDETERMINED"' in signature.text


@pytest.mark.parametrize(
    "field", ["reason", "facts", "candidate_causes", "missing_information", "next_checks"]
)
def test_assessment_rejects_blank_narratives(field: str) -> None:
    value = uncertain().model_dump()
    value[field] = "  " if field == "reason" else ["  "]
    with pytest.raises(ValidationError):
        DiagnosisAssessment.model_validate(value)


@pytest.mark.parametrize("retry", ["RETRYABLE", "NOT_RETRYABLE", "UNKNOWN"])
def test_abstention_cannot_recommend_retry_without_a_diagnosis(retry: str) -> None:
    response = ai_response(evidence=[]).model_copy(
        update={"assessment": uncertain(), "retry_decision": RetryDecision(retry)}
    )
    result = validate_ai_evidence(response, [], log_processor=LogProcessor())
    assert result.status == "PASSED"
    assert result.response.retry_decision == "UNKNOWN"
    assert result.response.operator_review_required
    assert not result.rejections


@pytest.mark.parametrize("basis", ["INFERRED", "UNDETERMINED"])
@pytest.mark.parametrize("missing_field", ["missing_information", "next_checks"])
def test_uncertain_assessment_requires_actionable_missing_information(
    basis: str, missing_field: str
) -> None:
    value = uncertain().model_dump()
    value["basis"] = basis
    value[missing_field] = []
    with pytest.raises(ValidationError):
        DiagnosisAssessment.model_validate(value)


@pytest.mark.parametrize(
    "root_cause", ["HTTP 401 caused the failure.", "password=original-secret caused it."]
)
def test_abstention_never_exposes_a_known_cause(root_cause: str) -> None:
    response = ai_response(root_cause=root_cause, evidence=[]).model_copy(
        update={"assessment": uncertain(), "retry_decision": RetryDecision.RETRYABLE}
    )
    result = validate_ai_evidence(response, [], log_processor=LogProcessor())
    assert result.response.root_cause == "제공된 근거만으로 실패 원인을 판단할 수 없습니다."
    assert result.response.classification == "UNKNOWN"
    assert result.response.retry_decision == "UNKNOWN"
    assert "original-secret" not in result.response.model_dump_json()
    assert result.status == ("REJECTED" if "password=" in root_cause else "PASSED")
