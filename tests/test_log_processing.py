from __future__ import annotations

import re
from dataclasses import asdict

import pytest

from dagsentry.log_processing import (
    MASK,
    LogInputTooLargeError,
    LogProcessingConfig,
    LogProcessor,
)
from dagsentry.task_logs import (
    LogCollectionStatus,
    LogUnavailableReason,
    TaskLogResult,
)


def excerpt_text(raw_log: str, config: LogProcessingConfig | None = None) -> str:
    result = LogProcessor(config).process(raw_log)
    return "\n".join(line.text for line in result.lines)


@pytest.mark.parametrize(
    ("raw_secret", "secret"),
    [
        ("password=hunter2", "hunter2"),
        ('"access_token": "access-123"', "access-123"),
        ("api-key=key-123", "key-123"),
        ("client_secret: client-123", "client-123"),
        ("Authorization: Bearer bearer-123", "bearer-123"),
        ("postgresql://orders:db-password@database/orders", "db-password"),
    ],
)
def test_secret_masking_corpus(raw_secret: str, secret: str) -> None:
    output = excerpt_text(f"RuntimeError: request failed {raw_secret}")

    assert secret not in output
    assert MASK in output


def test_custom_secret_pattern_is_applied_before_normalization() -> None:
    secret = "123e4567-e89b-42d3-a456-426614174000"
    config = LogProcessingConfig(custom_secret_patterns=(re.escape(secret),))

    output = excerpt_text(f"RuntimeError: tenant secret {secret}", config)

    assert secret not in output
    assert MASK in output
    assert "<UUID>" not in output


def test_airflow_noise_and_repeated_lines_are_removed() -> None:
    raw_log = """::group::Log message source details
/opt/airflow/logs/task.log
::endgroup::
[2026-08-09T00:00:00Z] Dependencies all met
same context
same context
RuntimeError: failure
"""

    result = LogProcessor().process(raw_log)
    texts = [line.text for line in result.lines]

    assert texts == ["same context", "RuntimeError: failure"]
    assert [line.line_id for line in result.lines] == [5, 7]


def test_extracts_exception_vendor_code_and_application_frame() -> None:
    raw_log = """Traceback (most recent call last):
  File "/usr/local/lib/python3.11/site-packages/oracledb/connection.py", line 10, in connect
    open_socket()
  File "/opt/airflow/dags/orders.py", line 42, in load_orders
    connection.connect()
oracledb.DatabaseError: ORA-12541: TNS:no listener
"""

    result = LogProcessor().process(raw_log)

    assert result.exception_classes == ("oracledb.DatabaseError",)
    assert result.vendor_error_codes == ("ORA-12541",)
    assert result.application_stack_frames == (
        'File "/opt/airflow/dags/orders.py", line <LINE>, in load_orders',
    )
    assert [line.line_id for line in result.lines] == [1, 2, 3, 4, 5, 6]


def test_extracts_bracketed_sqlstate_vendor_code() -> None:
    result = LogProcessor().process("DatabaseError: SQLSTATE[08001]")

    assert result.vendor_error_codes == ("SQLSTATE[08001]",)


def test_dynamic_values_normalize_to_stable_excerpt_and_line_ids() -> None:
    first = (
        "2026-08-09T01:02:03Z ERROR RuntimeError request_id=123456789 "
        "uuid=123e4567-e89b-42d3-a456-426614174000 /tmp/run-a/output 0xabcdef12"
    )
    second = (
        "2026-08-10T04:05:06Z ERROR RuntimeError request_id=987654321 "
        "uuid=987e6543-e21b-42d3-a456-426614174999 /tmp/run-b/output 0x1234abcd"
    )

    first_result = LogProcessor().process(first)
    second_result = LogProcessor().process(second)

    assert first_result.lines == second_result.lines
    assert first_result.lines[0].line_id == 1
    assert first_result.lines[0].text == (
        "<TIMESTAMP> ERROR RuntimeError request_id=<ID> uuid=<UUID> <TEMP_PATH> <HEX>"
    )


@pytest.mark.parametrize(
    "home_path",
    [
        "/Users/private-user/project/dags/orders.py",
        "/home/private-user/project/dags/orders.py",
        r"C:\Users\private-user\project\dags\orders.py",
    ],
)
def test_user_home_path_is_masked_in_excerpt_and_application_frame(home_path: str) -> None:
    result = LogProcessor().process(
        f'  File "{home_path}", line 42, in load_orders\nRuntimeError: failed'
    )
    serialized = repr(asdict(result))

    assert "private-user" not in serialized
    assert "<HOME_PATH>" in serialized


def test_same_input_produces_identical_excerpt() -> None:
    raw_log = "Traceback (most recent call last):\nValueError: invalid value"
    processor = LogProcessor()

    assert processor.process(raw_log) == processor.process(raw_log)


def test_legacy_airflow_timestamp_is_normalized() -> None:
    output = excerpt_text("[2026-08-09, 01:02:03 UTC] ERROR failure")

    assert output == "[<TIMESTAMP>] ERROR failure"


def test_excerpt_limits_keep_last_relevant_lines_in_original_order() -> None:
    raw_log = "\n".join(f"ERROR failure {index}" for index in range(10))
    config = LogProcessingConfig(max_excerpt_lines=3, max_excerpt_chars=31, context_lines=0)

    result = LogProcessor(config).process(raw_log)

    assert [line.line_id for line in result.lines] == [9, 10]
    assert sum(len(line.text) for line in result.lines) <= 31
    assert result.lines[-1].text == "ERROR failure 9"


def test_input_limit_is_enforced_before_processing() -> None:
    processor = LogProcessor(LogProcessingConfig(max_input_chars=5))

    with pytest.raises(LogInputTooLargeError):
        processor.process("123456")


def test_secret_never_appears_in_excerpt_or_serialized_result() -> None:
    secret = "do-not-store-this"
    result = LogProcessor().process(f"ValueError: password={secret}")

    assert secret not in repr(result)
    assert secret not in repr(asdict(result))


@pytest.mark.parametrize(
    "raw_log",
    ["", "plain context only", "\x00\x01", "한글 오류 메시지", "\n\n\n", "Error"],
)
def test_sparse_or_unusual_logs_do_not_raise(raw_log: str) -> None:
    result = LogProcessor().process(raw_log)

    assert result.input_line_count >= 0


def test_log_unavailable_is_a_normal_empty_processing_path() -> None:
    unavailable = TaskLogResult(
        LogCollectionStatus.LOG_UNAVAILABLE,
        None,
        LogUnavailableReason.TIMEOUT,
        0,
        0,
    )

    assert LogProcessor().process_result(unavailable) is None


def test_invalid_custom_pattern_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="Invalid custom Secret pattern"):
        LogProcessor(LogProcessingConfig(custom_secret_patterns=("[",)))
