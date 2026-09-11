"""OpenAI Responses API adapter for optional daily report narrative."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from dagsentry.config import Settings
from dagsentry.domain.reporting import (
    DailyReportAISummary,
    DailyReportSummaryProviderError,
    DailyStatistics,
    RuleBasedDailyReport,
)
from dagsentry.prompts import DAILY_REPORT_INSTRUCTIONS
from dagsentry.providers.openai import OpenAIProviderConfig
from dagsentry.providers.report_summary import report_input, request_summary

logger = logging.getLogger(__name__)


class OpenAIReportSummaryProvider:
    """Generate narrative-only report fields from immutable calculated inputs."""

    name = "openai"

    def __init__(
        self,
        config: OpenAIProviderConfig,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_client = http_client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIReportSummaryProvider:
        """Build the Provider only when the shared OpenAI settings are complete."""
        if settings.llm_provider != "openai":
            raise ValueError("OpenAI LLM Provider is not enabled")
        if settings.openai_api_key is None or settings.llm_model is None:
            raise ValueError("OpenAI API key and LLM model must be configured")
        return cls(
            OpenAIProviderConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.llm_model,
                prompt_version=settings.daily_report_prompt_version,
                base_url=settings.openai_api_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_attempts=settings.llm_max_attempts,
                retry_backoff_seconds=settings.llm_retry_backoff_seconds,
                trusted_http_hosts=settings.insecure_http_trusted_hosts,
            )
        )

    def summarize(
        self,
        statistics: DailyStatistics,
        rule_based_report: RuleBasedDailyReport,
    ) -> DailyReportAISummary:
        """Return schema-conforming prose or a sanitized fallback-triggering error."""
        response = request_summary(
            self.http_client,
            url=f"{self.config.base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            body=self._request_body(statistics, rule_based_report),
            timeout=self.config.timeout_seconds,
            attempts=self.config.max_attempts,
            backoff=self.config.retry_backoff_seconds,
            sleep=self.sleep,
        )
        return self._parse_response(response)

    def _request_body(
        self,
        statistics: DailyStatistics,
        rule_based_report: RuleBasedDailyReport,
    ) -> dict[str, object]:
        return {
            "model": self.config.model,
            "store": False,
            "instructions": DAILY_REPORT_INSTRUCTIONS,
            "input": report_input(statistics, rule_based_report),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "dagsentry_daily_report_summary",
                    "strict": True,
                    "schema": DailyReportAISummary.model_json_schema(),
                }
            },
        }

    def _parse_response(self, response: httpx.Response) -> DailyReportAISummary:
        try:
            body: dict[str, Any] = response.json()
            summary = DailyReportAISummary.model_validate_json(_find_output_text(body))
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ):
            logger.warning(
                "OpenAI returned an invalid daily report summary request_id=%s",
                response.headers.get("x-request-id"),
            )
            raise DailyReportSummaryProviderError(
                "OpenAI returned an invalid report summary"
            ) from None
        return summary


def _find_output_text(body: dict[str, Any]) -> str:
    for item in body["output"]:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text: object = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise ValueError("response has no output text")
