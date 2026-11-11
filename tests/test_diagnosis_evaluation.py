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
    model_response = report["results"][1]["model_response"]
    effective_response = report["results"][1]["response"]
    assert model_response["classification"] == "DAG_CODE"
    assert model_response["root_cause"] == (
        "password=[REDACTED]" if "password=" in root_cause else root_cause
    )
    assert effective_response["classification"] == "DAG_CODE"
    assert effective_response["root_cause"] == "제공된 근거만으로 실패 원인을 판단할 수 없습니다."
    assert model_response["retry_decision"] == "RETRYABLE"
    assert report["results"][1]["response"]["retry_decision"] == "UNKNOWN"
    assert report["results"][1]["semantic_verdict"] == "PENDING_HUMAN_REVIEW"
    assert provider.diagnose.call_count == 2
    assert "eval-secret" not in output.read_text()


@pytest.mark.parametrize("live", [False, True])
def test_codex_evaluation_requires_live_and_bypasses_runtime_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live: bool,
) -> None:
    from dagsentry.evaluation_codex import CodexEvaluationProvider
    from dagsentry.llm import AIDiagnosisRequest

    spec = importlib.util.spec_from_file_location(
        "diagnosis_evaluation_codex", Path(__file__).parents[1] / "scripts/evaluate-diagnosis.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[AIDiagnosisRequest] = []

    class StubCodexProvider(CodexEvaluationProvider):
        def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
            calls.append(request)
            assert self.model == "explicit-model"
            assert self.reasoning_effort == "medium"
            self.cli_version = "codex-cli test"
            return LLMResult(
                ai_response(evidence=[], root_cause="판단 불가").model_copy(
                    update={"assessment": uncertain()}
                ),
                LLMCallMetadata(None, self.model, "test-prompt", 1, 10, 10),
            )

    monkeypatch.setattr(module, "CodexEvaluationProvider", StubCodexProvider)
    forbidden = Mock(
        side_effect=AssertionError("Codex evaluation must not resolve runtime providers")
    )
    monkeypatch.setattr(module, "ConfiguredLLMConnectionResolver", forbidden)
    monkeypatch.setattr(module, "create_session_factory", forbidden)
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {"id": "first", "log": "", "allowed_basis": ["UNDETERMINED"], "review": "abstain"},
            ]
        )
    )
    output = tmp_path / "output.json"
    argv = [
        "evaluate",
        "--cases",
        str(cases),
        "--output",
        str(output),
        "--codex-model",
        "explicit-model",
    ]
    if live:
        argv.append("--live")
    monkeypatch.setattr("sys.argv", argv)
    module.main()
    report = json.loads(output.read_text())
    if live:
        assert len(calls) == 1
        assert report["run"]["configured_model"] == "explicit-model"
        assert report["run"]["cli_version"] == "codex-cli test"
        assert report["run"]["reasoning_effort"] == "medium"
        assert report["run"]["max_attempts"] == 1
        assert report["results"][0]["request"] == json.loads(json.dumps(calls[0].to_json_value()))
    else:
        assert not calls
        assert report["status"] == "NOT_RUN"
