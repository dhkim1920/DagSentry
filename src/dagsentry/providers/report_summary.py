"""Shared report serialization and bounded HTTP transport."""

import json
from collections.abc import Callable

import httpx

from dagsentry.domain.reporting import (
    DailyReportSummaryProviderError,
    DailyStatistics,
    RuleBasedDailyReport,
)


def report_input(statistics: DailyStatistics, report: RuleBasedDailyReport) -> str:
    return json.dumps(
        {
            "statistics": statistics.model_dump(mode="json"),
            "rule_based_report": report.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def request_summary(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, object],
    timeout: float,
    attempts: int,
    backoff: float,
    sleep: Callable[[float], None],
) -> httpx.Response:
    for attempt in range(attempts):
        try:
            response = client.post(url, headers=headers, json=body, timeout=timeout)
        except httpx.TransportError:
            if attempt + 1 < attempts:
                sleep(backoff)
                continue
            raise DailyReportSummaryProviderError(
                "Report summary request failed after retry"
            ) from None
        if response.status_code in {408, 429} or response.status_code >= 500:
            if attempt + 1 < attempts:
                sleep(backoff)
                continue
            raise DailyReportSummaryProviderError("Report summary request failed after retry")
        if response.status_code >= 400:
            raise DailyReportSummaryProviderError(
                f"Report summary request failed with HTTP {response.status_code}"
            )
        return response
    raise RuntimeError("unreachable report summary request")
