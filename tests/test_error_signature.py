from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dagsentry.error_signature import (
    FINGERPRINT_VERSION,
    ErrorSignatureCandidate,
    ErrorSignatureInput,
    SignatureStatus,
    build_error_signature,
    persist_error_signature,
    signature_input_from_excerpt,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.models import ErrorSignatureRecord


def candidate(raw_log: str, operator_type: str = "PythonOperator") -> ErrorSignatureCandidate:
    excerpt = LogProcessor().process(raw_log)
    return build_error_signature(signature_input_from_excerpt(operator_type, excerpt))


def test_canonical_json_has_fixed_keys_nulls_and_whitespace_normalization() -> None:
    signature = build_error_signature(
        ErrorSignatureInput(
            operator_type="  PythonOperator  ",
            exception_class=" ValueError ",
            vendor_error_code=None,
            normalized_message="ValueError:   invalid order",
            application_stack_frame=None,
        )
    )

    assert signature.status == SignatureStatus.SIGNABLE
    assert signature.fingerprint_version == FINGERPRINT_VERSION == 1
    assert signature.canonical_json == (
        '{"application_stack_frame":null,"exception_class":"ValueError",'
        '"normalized_message":"ValueError: invalid order","operator_type":"PythonOperator",'
        '"vendor_error_code":null}'
    )
    assert (
        signature.fingerprint == "47d1cf7af4257a5c2cd2e17151a7287bdbcc0af63ec94966debf28887ab8cd86"
    )


def test_excerpt_fields_build_expected_canonical_error() -> None:
    raw_log = """Traceback (most recent call last):
  File "/opt/airflow/dags/orders.py", line 42, in load_orders
    transform()
ValueError: invalid order
"""

    signature = candidate(raw_log)

    assert signature.canonical_error is not None
    assert signature.canonical_error.exception_class == "ValueError"
    assert signature.canonical_error.normalized_message == "ValueError: invalid order"
    assert signature.canonical_error.application_stack_frame == (
        'File "/opt/airflow/dags/orders.py", line <LINE>, in load_orders'
    )


def test_dynamic_values_produce_same_signature() -> None:
    first = candidate(
        "2026-08-09T01:02:03Z ERROR request_id=123456789 "
        "uuid=123e4567-e89b-42d3-a456-426614174000 failed"
    )
    second = candidate(
        "2026-08-10T04:05:06Z ERROR request_id=987654321 "
        "uuid=987e6543-e21b-72d3-a456-426614174999 failed"
    )

    assert first.status == SignatureStatus.SIGNABLE
    assert first.canonical_json == second.canonical_json
    assert first.fingerprint == second.fingerprint


def test_meaningfully_different_message_or_context_produces_different_signature() -> None:
    invalid_order = candidate("ValueError: invalid order")
    missing_order = candidate("ValueError: missing order")
    different_operator = candidate("ValueError: invalid order", "BashOperator")

    assert invalid_order.fingerprint != missing_order.fingerprint
    assert invalid_order.fingerprint != different_operator.fingerprint


def test_application_frame_separates_otherwise_identical_errors() -> None:
    first = candidate(
        'File "/opt/airflow/dags/orders.py", line 10, in load_orders\nValueError: invalid'
    )
    second = candidate(
        'File "/opt/airflow/dags/customers.py", line 20, in load_customers\nValueError: invalid'
    )

    assert first.fingerprint != second.fingerprint


def test_insufficient_information_is_unsignable() -> None:
    signature = candidate("task context without a diagnostic signal")

    assert signature.status == SignatureStatus.UNSIGNABLE
    assert signature.canonical_error is None
    assert signature.canonical_json is None
    assert signature.fingerprint is None


def test_persistence_is_idempotent(session: Session) -> None:
    signature = candidate("ValueError: invalid order")

    first = persist_error_signature(session, signature)
    duplicate = persist_error_signature(session, signature)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.signature_id == first.signature_id
    assert session.scalar(select(func.count()).select_from(ErrorSignatureRecord)) == 1


def test_persisted_record_contains_canonical_fields(session: Session) -> None:
    signature = candidate("oracledb.DatabaseError: ORA-12541: no listener", "OracleOperator")

    result = persist_error_signature(session, signature)

    assert result.signature_id is not None
    record = session.get(ErrorSignatureRecord, result.signature_id)
    assert record is not None
    assert record.fingerprint_version == FINGERPRINT_VERSION
    assert record.fingerprint == signature.fingerprint
    assert record.operator_type == "OracleOperator"
    assert record.exception_class == "oracledb.DatabaseError"
    assert record.vendor_error_code == "ORA-12541"


def test_unsignable_candidate_is_not_persisted(session: Session) -> None:
    signature = candidate("plain task context")

    result = persist_error_signature(session, signature)

    assert result.status == SignatureStatus.UNSIGNABLE
    assert result.signature_id is None
    assert result.created is False
    assert session.scalar(select(func.count()).select_from(ErrorSignatureRecord)) == 0
