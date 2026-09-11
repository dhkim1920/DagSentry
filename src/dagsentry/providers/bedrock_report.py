"""Bedrock Converse report narrative with bounded SDK calls."""

import json
import time
from collections.abc import Callable

from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from dagsentry.config import Settings
from dagsentry.domain.reporting import (
    DailyReportAISummary,
    DailyReportSummaryProviderError,
    DailyStatistics,
    RuleBasedDailyReport,
)
from dagsentry.prompts import DAILY_REPORT_INSTRUCTIONS
from dagsentry.providers.bedrock import (
    _TRANSIENT_AWS_ERRORS,
    BedrockProvider,
    BedrockProviderConfig,
    BedrockRuntimeClient,
    _create_client,
)
from dagsentry.providers.report_summary import report_input


class BedrockReportSummaryProvider:
    name = "bedrock"

    def __init__(
        self,
        config: BedrockProviderConfig,
        client: BedrockRuntimeClient | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.client = client or _create_client(config)
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "BedrockReportSummaryProvider":
        provider = BedrockProvider.from_settings(
            settings.model_copy(update={"llm_prompt_version": settings.daily_report_prompt_version})
        )
        return cls(provider.config, provider.client)

    def summarize(
        self, statistics: DailyStatistics, rule_based_report: RuleBasedDailyReport
    ) -> DailyReportAISummary:
        schema = DailyReportAISummary.model_json_schema()
        # Bedrock does not support array length constraints in structured output.
        for property_schema in schema["properties"].values():
            property_schema.pop("minItems", None)
            property_schema.pop("maxItems", None)
        for attempt in range(self.config.max_attempts):
            try:
                response = self.client.converse(
                    modelId=self.config.model_id,
                    system=[{"text": DAILY_REPORT_INSTRUCTIONS}],
                    messages=[
                        {
                            "role": "user",
                            "content": [{"text": report_input(statistics, rule_based_report)}],
                        }
                    ],
                    inferenceConfig={"maxTokens": self.config.max_output_tokens},
                    outputConfig={
                        "textFormat": {
                            "type": "json_schema",
                            "structure": {
                                "jsonSchema": {
                                    "schema": json.dumps(schema),
                                    "name": "dagsentry_daily_report_summary",
                                }
                            },
                        }
                    },
                )
            except (NoCredentialsError, PartialCredentialsError):
                raise DailyReportSummaryProviderError(
                    "Bedrock credentials are unavailable"
                ) from None
            except ClientError as error:
                if (
                    error.response.get("Error", {}).get("Code") in _TRANSIENT_AWS_ERRORS
                    and attempt + 1 < self.config.max_attempts
                ):
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                raise DailyReportSummaryProviderError("Bedrock report request failed") from None
            except BotoCoreError:
                if attempt + 1 < self.config.max_attempts:
                    self.sleep(self.config.retry_backoff_seconds)
                    continue
                raise DailyReportSummaryProviderError(
                    "Bedrock report request failed after retry"
                ) from None
            try:
                if response["stopReason"] != "end_turn":
                    raise ValueError("incomplete summary")
                text = "".join(
                    item["text"]
                    for item in response["output"]["message"]["content"]
                    if "text" in item
                )
                return DailyReportAISummary.model_validate_json(text)
            except (ValueError, KeyError, TypeError):
                raise DailyReportSummaryProviderError(
                    "Bedrock returned an invalid report summary"
                ) from None
        raise RuntimeError("unreachable Bedrock report request")
