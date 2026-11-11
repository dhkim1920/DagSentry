from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from dagsentry.config import Settings
from dagsentry.domain.diagnosis import RetryDecision
from dagsentry.llm import LLMCallMetadata, LLMProviderError, LLMResult
from tests.test_diagnosis_assessment import uncertain
from tests.test_evidence_validation import ai_response


@pytest.mark.parametrize("root_cause", ["판단 불가", "password=eval-secret"])
def test_live_evaluation_keeps_failed_case_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_cause: str
) -> None:
    spec = importlib.util.spec_from_file_location(
        "diagnosis_evaluation", Path(__file__).parents[1] / "scripts/evaluate-diagnosis.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = ai_response(evidence=[], root_cause=root_cause).model_copy(
        update={"assessment": uncertain(), "retry_decision": RetryDecision.RETRYABLE}
    )
    provider = Mock()
    provider.diagnose.side_effect = [
        LLMProviderError("Invalid structured response"),
        LLMResult(response, LLMCallMetadata(None, "test-model", "test-prompt", 1, 10, 10)),
    ]
    resolver = Mock()
    resolver.resolve.return_value.provider = provider
    monkeypatch.setattr(module, "ConfiguredLLMConnectionResolver", lambda *args: resolver)
    monkeypatch.setattr(module, "create_session_factory", lambda *args: None)
    monkeypatch.setattr(module, "Settings", lambda: Settings())
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {"id": case, "log": "", "allowed_basis": ["UNDETERMINED"], "review": "abstain"}
                for case in ["first", "second"]
            ]
        )
    )
    output = tmp_path / "output.json"
    monkeypatch.setattr(
        "sys.argv", ["evaluate", "--cases", str(cases), "--output", str(output), "--live"]
    )
    module.main()
    report = json.loads(output.read_text())
    assert report["case_count"] == report["attempted_cases"] == 2
    assert report["provider_error_cases"] == 1
    assert [row["id"] for row in report["results"]] == ["first", "second"]
    assert report["results"][0]["semantic_verdict"] == "NOT_GRADED_PROVIDER_ERROR"
    assert report["results"][1]["model_response"]["retry_decision"] == "RETRYABLE"
    assert report["results"][1]["response"]["retry_decision"] == "UNKNOWN"
    assert report["results"][1]["semantic_verdict"] == "PENDING_HUMAN_REVIEW"
    assert provider.diagnose.call_count == 2
    assert "eval-secret" not in output.read_text()
