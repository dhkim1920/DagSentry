import json
import logging

from dagsentry.observability import JsonFormatter, reset_correlation_id, set_correlation_id


def test_json_log_contains_required_fields_and_correlation_id() -> None:
    formatter = JsonFormatter("api")
    record = logging.LogRecord(
        name="dagsentry.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="failure accepted",
        args=(),
        exc_info=None,
    )
    token = set_correlation_id("d8dce2a3-d335-429c-a3e2-37f1d2bfe182")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        reset_correlation_id(token)

    assert payload["level"] == "INFO"
    assert payload["service"] == "api"
    assert payload["message"] == "failure accepted"
    assert payload["correlation_id"] == "d8dce2a3-d335-429c-a3e2-37f1d2bfe182"
    assert payload["timestamp"].endswith("+00:00")
