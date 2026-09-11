"""Anthropic Messages report narrative adapter."""

import time
from collections.abc import Callable

import httpx

from dagsentry.config import Settings
from dagsentry.domain.reporting import (
    DailyReportAISummary,
    DailyReportSummaryProviderError,
    DailyStatistics,
    RuleBasedDailyReport,
)
from dagsentry.prompts import DAILY_REPORT_INSTRUCTIONS
from dagsentry.providers.anthropic import AnthropicProvider, AnthropicProviderConfig
from dagsentry.providers.report_summary import report_input, request_summary


class AnthropicReportSummaryProvider:
    name = "anthropic"

    def __init__(
        self,
        config: AnthropicProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnthropicReportSummaryProvider":
        provider = AnthropicProvider.from_settings(
            settings.model_copy(update={"llm_prompt_version": settings.daily_report_prompt_version})
        )
        return cls(provider.config, provider.http_client)

    def summarize(
        self, statistics: DailyStatistics, rule_based_report: RuleBasedDailyReport
    ) -> DailyReportAISummary:
        response = request_summary(
            self.http_client,
            url=f"{self.config.base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": self.config.api_version,
            },
            body={
                "model": self.config.model,
                "max_tokens": self.config.max_output_tokens,
                "system": DAILY_REPORT_INSTRUCTIONS,
                "messages": [
                    {"role": "user", "content": report_input(statistics, rule_based_report)}
                ],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": DailyReportAISummary.model_json_schema(),
                    }
                },
            },
            timeout=self.config.timeout_seconds,
            attempts=self.config.max_attempts,
            backoff=self.config.retry_backoff_seconds,
            sleep=self.sleep,
        )
        try:
            body = response.json()
            if body["stop_reason"] != "end_turn":
                raise ValueError("incomplete summary")
            text = "".join(item["text"] for item in body["content"] if item["type"] == "text")
            return DailyReportAISummary.model_validate_json(text)
        except (ValueError, KeyError, TypeError):
            raise DailyReportSummaryProviderError(
                "Anthropic returned an invalid report summary"
            ) from None
