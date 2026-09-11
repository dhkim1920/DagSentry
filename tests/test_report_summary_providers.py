import json
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from dagsentry.daily_report import build_rule_based_report
from dagsentry.domain.reporting import DailyReportSummaryProviderError
from dagsentry.prompts import DAILY_REPORT_INSTRUCTIONS
from dagsentry.providers.anthropic import AnthropicProviderConfig
from dagsentry.providers.anthropic_report import AnthropicReportSummaryProvider
from dagsentry.providers.bedrock import BedrockProviderConfig
from dagsentry.providers.bedrock_report import BedrockReportSummaryProvider
from tests.test_daily_report import statistics

SUMMARY = {
    "key_changes": ["네트워크 실패가 발생했습니다."],
    "priorities": ["미해결 장애를 점검하세요."],
}


@pytest.mark.parametrize("vendor", ["anthropic", "bedrock"])
@pytest.mark.parametrize(
    "scenario", ["success", "retry", "permanent", "malformed", "schema", "exhausted"]
)
def test_report_summary_contract(vendor: str, scenario: str) -> None:
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []
    source = statistics()
    report = build_rule_based_report(source)
    text = (
        "{"
        if scenario == "malformed"
        else json.dumps({**SUMMARY, "statistics": "forbidden"} if scenario == "schema" else SUMMARY)
    )

    def capture(body: dict[str, Any]) -> int:
        calls.append(body)
        assert DAILY_REPORT_INSTRUCTIONS in json.dumps(body)
        if scenario == "permanent":
            return 403
        if scenario == "exhausted" or (scenario == "retry" and len(calls) == 1):
            return 503
        return 200

    if vendor == "anthropic":

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert json.loads(body["messages"][0]["content"])["statistics"] == source.model_dump(
                mode="json"
            )
            status = capture(body)
            return httpx.Response(
                status,
                json={"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]},
            )

        provider = AnthropicReportSummaryProvider(
            AnthropicProviderConfig(
                api_key="disposable", model="test-model", prompt_version="daily-report-ko-v1"
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=sleeps.append,
        )
    else:

        class Client:
            def converse(self, **kwargs: object) -> dict[str, Any]:
                status = capture(kwargs)
                if status != 200:
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "AccessDeniedException"
                                if status == 403
                                else "ServiceUnavailableException",
                                "Message": "disposable-secret",
                            }
                        },
                        "Converse",
                    )
                return {
                    "stopReason": "end_turn",
                    "output": {"message": {"content": [{"text": text}]}},
                }

        bedrock = BedrockReportSummaryProvider(
            BedrockProviderConfig(
                region="us-east-1", model_id="test-model", prompt_version="daily-report-ko-v1"
            ),
            client=Client(),
            sleep=sleeps.append,
        )
        if scenario in {"success", "retry"}:
            assert bedrock.summarize(source, report).model_dump(mode="json") == SUMMARY
        else:
            with pytest.raises(DailyReportSummaryProviderError) as error:
                bedrock.summarize(source, report)
            assert "disposable-secret" not in str(error.value)
        assert len(calls) == (2 if scenario in {"retry", "exhausted"} else 1)
        return
    if scenario in {"success", "retry"}:
        assert provider.summarize(source, report).model_dump(mode="json") == SUMMARY
    else:
        with pytest.raises(DailyReportSummaryProviderError):
            provider.summarize(source, report)
    assert len(calls) == (2 if scenario in {"retry", "exhausted"} else 1)
