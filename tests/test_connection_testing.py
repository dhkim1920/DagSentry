from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from dagsentry.connection_testing import (
    ConnectionTestSnapshot,
    UnsupportedConnectionTestError,
)
from dagsentry.connection_testing import test_connection as run_connection_test
from dagsentry.domain.connection import (
    ConnectionProvider,
    ConnectionPurpose,
    ConnectionTestErrorCategory,
    ConnectionTestStatus,
)


def test_airflow_test_reads_version_with_optional_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://airflow.example/api/v2/version"
        assert request.headers["Authorization"] == "Bearer airflow-token"
        return httpx.Response(200, json={"version": "3.2.0"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = run_connection_test(
            snapshot(
                ConnectionProvider.AIRFLOW,
                ConnectionPurpose.AIRFLOW,
                {"api_base_url": "https://airflow.example/api/v2", "timeout_seconds": 3},
                {"token": "airflow-token"},
            ),
            client,
        )

    assert outcome.status == ConnectionTestStatus.PASSED
    assert outcome.error_category is None


def test_ollama_test_reads_tags_without_generating_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "http://ollama:11434/api/tags"
        return httpx.Response(200, json={"models": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = run_connection_test(
            snapshot(
                ConnectionProvider.OLLAMA,
                ConnectionPurpose.LLM,
                {"api_base_url": "http://ollama:11434/api", "timeout_seconds": 5.0},
            ),
            client,
        )

    assert outcome.status == ConnectionTestStatus.PASSED
    assert outcome.error_category is None


def test_slack_test_calls_only_auth_and_channel_read_endpoints() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        if request.url.path.endswith("/auth.test"):
            assert request.method == "POST"
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        assert request.method == "GET"
        assert request.url.path.endswith("/conversations.info")
        assert request.url.params.get("channel") == "C1"
        return httpx.Response(200, json={"ok": True, "channel": {"id": "C1"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = run_connection_test(
            snapshot(
                ConnectionProvider.SLACK,
                ConnectionPurpose.NOTIFICATION,
                {
                    "api_base_url": "https://slack.com/api",
                    "channel": "C1",
                    "timeout_seconds": 5.0,
                },
                {"bot_token": "xoxb-test"},
            ),
            client,
        )

    assert outcome.status == ConnectionTestStatus.PASSED
    assert paths == ["/api/auth.test", "/api/conversations.info"]
    assert "/chat.postMessage" not in paths


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401), ConnectionTestErrorCategory.AUTHENTICATION),
        (httpx.Response(403), ConnectionTestErrorCategory.AUTHORIZATION),
        (httpx.Response(429), ConnectionTestErrorCategory.RATE_LIMITED),
        (httpx.Response(503), ConnectionTestErrorCategory.UNAVAILABLE),
        (httpx.Response(400), ConnectionTestErrorCategory.INVALID_REQUEST),
        (
            httpx.Response(200, json={"unexpected": True}),
            ConnectionTestErrorCategory.INVALID_RESPONSE,
        ),
    ],
)
def test_test_failures_return_only_bounded_categories(
    response: httpx.Response,
    expected: ConnectionTestErrorCategory,
) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        outcome = run_connection_test(
            snapshot(
                ConnectionProvider.OLLAMA,
                ConnectionPurpose.LLM,
                {"api_base_url": "http://ollama:11434/api", "timeout_seconds": 5.0},
            ),
            client,
        )

    assert outcome.status == ConnectionTestStatus.FAILED
    assert outcome.error_category == expected


def test_timeout_is_bounded_and_side_effectful_provider_is_rejected() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider body must not escape")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        outcome = run_connection_test(
            snapshot(
                ConnectionProvider.OLLAMA,
                ConnectionPurpose.LLM,
                {"api_base_url": "http://ollama:11434/api", "timeout_seconds": 5.0},
            ),
            client,
        )
        with pytest.raises(UnsupportedConnectionTestError, match="not supported"):
            run_connection_test(
                snapshot(
                    ConnectionProvider.WEBHOOK,
                    ConnectionPurpose.NOTIFICATION,
                    {"timeout_seconds": 5.0},
                ),
                client,
            )

    assert outcome.error_category == ConnectionTestErrorCategory.TIMEOUT


def snapshot(
    provider: ConnectionProvider,
    purpose: ConnectionPurpose,
    config: dict[str, object],
    secret: dict[str, str] | None = None,
) -> ConnectionTestSnapshot:
    return ConnectionTestSnapshot(
        id=uuid4(),
        environment="production",
        purpose=purpose,
        provider=provider,
        non_secret_config=config,
        secret=secret or {},
        version=1,
    )
