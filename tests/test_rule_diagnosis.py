from __future__ import annotations

from dataclasses import replace

import pytest

from dagsentry.domain.diagnosis import RetryDecision
from dagsentry.log_processing import LogProcessor
from dagsentry.rule_diagnosis import (
    RULESET_VERSION,
    ErrorClassification,
    ExtractedValue,
    RuleDiagnosisInput,
    RuleDiagnosisResult,
    RuleEngine,
    default_rules,
)
from dagsentry.task_logs import (
    LogCollectionStatus,
    LogUnavailableReason,
    TaskLogResult,
)


def diagnose(raw_log: str, *, operator_type: str | None = "PythonOperator") -> RuleDiagnosisResult:
    excerpt = LogProcessor().process(raw_log)
    return RuleEngine().diagnose(RuleDiagnosisInput(operator_type, excerpt))


@pytest.mark.parametrize(
    ("raw_log", "classification", "rule_id", "extracted_value"),
    [
        (
            "oracledb.DatabaseError: ORA-12541: TNS:no listener",
            ErrorClassification.SOURCE_DATABASE,
            "oracle.no_listener.v1",
            ExtractedValue("vendor_error_code", "ORA-12541"),
        ),
        (
            "Request failed with HTTP 401",
            ErrorClassification.AUTHENTICATION,
            "http.authentication_401.v1",
            ExtractedValue("http_status", "401"),
        ),
        (
            "Response status_code=403",
            ErrorClassification.AUTHORIZATION,
            "http.authorization_403.v1",
            ExtractedValue("http_status", "403"),
        ),
        (
            "Pod terminated: OOMKilled",
            ErrorClassification.RESOURCE,
            "resource.out_of_memory.v1",
            ExtractedValue("resource_signal", "OUT_OF_MEMORY"),
        ),
    ],
)
def test_explicit_rules_return_structured_results(
    raw_log: str,
    classification: ErrorClassification,
    rule_id: str,
    extracted_value: ExtractedValue,
) -> None:
    result = diagnose(raw_log)

    assert result.classification == classification
    assert result.matched_rule == rule_id
    assert result.ruleset_version == RULESET_VERSION
    assert result.extracted_values == (extracted_value,)
    assert result.confidence >= 0.98
    assert result.confidence_reason
    assert result.evidence_line_ids == (1,)
    assert result.recommended_actions
    assert all(
        any("가" <= char <= "힣" for char in action) for action in result.recommended_actions
    )


def test_python_exception_requires_application_stack_frame() -> None:
    raw_log = """Traceback (most recent call last):
  File "/opt/airflow/dags/orders.py", line 42, in load_orders
    transform()
ValueError: invalid order
"""

    result = diagnose(raw_log)

    assert result.classification == ErrorClassification.DAG_CODE
    assert result.matched_rule == "python.application_exception.v1"
    assert result.extracted_values == (
        ExtractedValue("exception_class", "ValueError"),
        ExtractedValue(
            "application_stack_frame",
            'File "/opt/airflow/dags/orders.py", line <LINE>, in load_orders',
        ),
    )
    assert result.evidence_line_ids == (4,)
    assert result.recommended_actions
    assert result.retry_decision == RetryDecision.NOT_RETRYABLE


@pytest.mark.parametrize(
    ("raw_log", "classification"),
    [
        ("401 Client Error: Unauthorized", ErrorClassification.AUTHENTICATION),
        ("403 Forbidden", ErrorClassification.AUTHORIZATION),
    ],
)
def test_common_http_client_error_forms_are_classified(
    raw_log: str, classification: ErrorClassification
) -> None:
    assert diagnose(raw_log).classification == classification


def test_library_exception_without_application_frame_is_unknown() -> None:
    raw_log = """Traceback (most recent call last):
  File "/usr/local/lib/python3.11/site-packages/httpx/client.py", line 10, in send
    raise ValueError()
ValueError: invalid response
"""

    result = diagnose(raw_log)

    assert result.classification == ErrorClassification.UNKNOWN
    assert result.matched_rule == "unknown.v1"
    assert result.confidence == 0.0
    assert result.evidence_line_ids == ()
    assert result.recommended_actions
    assert result.retry_decision == RetryDecision.UNKNOWN


@pytest.mark.parametrize(
    "raw_log",
    [
        "processed 401 rows successfully",
        "service is listening on port 4030",
        "memory usage is high but within limit",
        "reference ORA-125410 is not an Oracle error code",
        "HTTP request completed successfully",
    ],
)
def test_similar_but_non_matching_text_stays_unknown(raw_log: str) -> None:
    assert diagnose(raw_log).classification == ErrorClassification.UNKNOWN


def test_specific_rule_wins_over_generic_python_exception() -> None:
    raw_log = """Traceback (most recent call last):
  File "/opt/airflow/dags/orders.py", line 42, in load_orders
    call_api()
RuntimeError: upstream returned HTTP 401
"""

    result = diagnose(raw_log)

    assert result.classification == ErrorClassification.AUTHENTICATION
    assert result.matched_rule == "http.authentication_401.v1"


def test_latest_evidence_breaks_equal_priority_conflict() -> None:
    raw_log = "HTTP 401 from first endpoint\nHTTP 403 from final endpoint"

    result = diagnose(raw_log)

    assert result.classification == ErrorClassification.AUTHORIZATION
    assert result.evidence_line_ids == (2,)


def test_log_unavailable_still_returns_unknown_rule_result() -> None:
    unavailable = TaskLogResult(
        LogCollectionStatus.LOG_UNAVAILABLE,
        None,
        LogUnavailableReason.TIMEOUT,
        0,
        0,
    )
    excerpt = LogProcessor().process_result(unavailable)

    result = RuleEngine().diagnose(RuleDiagnosisInput("PythonOperator", excerpt))

    assert result.classification == ErrorClassification.UNKNOWN
    assert result.matched_rule == "unknown.v1"


def test_rule_diagnosis_is_deterministic() -> None:
    diagnosis_input = RuleDiagnosisInput(
        "PythonOperator",
        LogProcessor().process("Request failed with HTTP 403"),
    )
    engine = RuleEngine()

    assert engine.diagnose(diagnosis_input) == engine.diagnose(diagnosis_input)


def test_classification_enum_matches_design_contract() -> None:
    assert {classification.value for classification in ErrorClassification} == {
        "DAG_CODE",
        "AIRFLOW_PLATFORM",
        "SOURCE_DATABASE",
        "NETWORK",
        "AUTHENTICATION",
        "AUTHORIZATION",
        "RESOURCE",
        "DATA_QUALITY",
        "EXTERNAL_SYSTEM",
        "CONFIGURATION",
        "UNKNOWN",
    }


def test_duplicate_rule_ids_are_rejected() -> None:
    rules = RuleEngine().rules

    with pytest.raises(ValueError, match="Rule IDs must be unique"):
        RuleEngine((rules[0], rules[0]))


def test_rule_id_breaks_identical_priority_and_line_ties() -> None:
    from dagsentry.rule_diagnosis import PatternRule

    rule = default_rules()[0]
    assert isinstance(rule, PatternRule)
    engine = RuleEngine((replace(rule, rule_id="z"), replace(rule, rule_id="a")))
    result = engine.diagnose(RuleDiagnosisInput(None, LogProcessor().process("HTTP 401")))
    assert result.matched_rule == "a"
