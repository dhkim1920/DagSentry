from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest

from dagsentry.daily_report import build_rule_based_report
from dagsentry.domain.diagnosis import ErrorClassification
from dagsentry.domain.reporting import (
    DailyReportSummaryProviderError,
    DailyStatistics,
    ErrorSignatureStatistics,
    IncidentStatistics,
    MeanTimeStatistics,
)
from dagsentry.providers.openai import OpenAIProviderConfig
from dagsentry.providers.openai_report import OpenAIReportSummaryProvider


def statistics() -> DailyStatistics:
    report_date = date(2026, 8, 12)
    start = datetime.combine(report_date, time.min, tzinfo=UTC)
    counts = dict.fromkeys(ErrorClassification, 0)
    counts[ErrorClassification.NETWORK] = 3
    return DailyStatistics(
        report_date=report_date,
        period_start=start,
        period_end=start + timedelta(days=1),
        environment="production",
        failure_attempts=3,
        affected_task_instances=2,
        affected_dag_runs=1,
        incidents=IncidentStatistics(new=1, unresolved=1, recovered=0),
        error_signatures=ErrorSignatureStatistics(new=1, repeated=2),
        classification_counts=counts,
        mean_time=MeanTimeStatistics(recovery_seconds=None, resolution_seconds=120.0),
    )


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_attempts: int = 2,
    sleep: Callable[[float], None] = lambda _: None,
) -> OpenAIReportSummaryProvider:
    return OpenAIReportSummaryProvider(
        OpenAIProviderConfig(
            api_key="secret",
            model="test-model",
            prompt_version="daily-report-v1",
            max_attempts=max_attempts,
            retry_backoff_seconds=0.2,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )


def response_body(value: dict[str, object]) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        ]
    }


def test_openai_report_provider_requests_narrative_only_schema() -> None:
    captured: dict[str, Any] | None = None

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = json.loads(request.content)
        return httpx.Response(
            200,
            json=response_body(
                {"key_changes": ["Network failures repeated."], "priorities": ["Investigate."]}
            ),
        )

    stats = statistics()
    summary = provider(handle).summarize(stats, build_rule_based_report(stats))

    assert summary.priorities == ("Investigate.",)
    assert captured is not None
    schema = captured["text"]["format"]["schema"]
    assert "statistics" not in schema["properties"]
    assert json.loads(captured["input"])["statistics"] == stats.model_dump(mode="json")


def test_openai_report_provider_rejects_attempt_to_replace_statistics() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response_body(
                {
                    "key_changes": ["Changed."],
                    "priorities": ["Act."],
                    "statistics": {"failure_attempts": 999},
                }
            ),
        )

    stats = statistics()
    with pytest.raises(DailyReportSummaryProviderError):
        provider(handle).summarize(stats, build_rule_based_report(stats))


def test_openai_report_provider_retries_transient_failure() -> None:
    calls = 0
    sleeps: list[float] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=response_body({"key_changes": ["Stable."], "priorities": ["Monitor."]}),
        )

    stats = statistics()
    provider(handle, sleep=sleeps.append).summarize(stats, build_rule_based_report(stats))

    assert calls == 2
    assert sleeps == [0.2]
