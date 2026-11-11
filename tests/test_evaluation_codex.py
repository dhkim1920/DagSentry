from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from dagsentry.evaluation_codex import CodexEvaluationProvider
from dagsentry.llm import LLMProviderError
from dagsentry.prompts import AI_DIAGNOSIS_INSTRUCTIONS, AI_DIAGNOSIS_PROMPT_VERSION
from tests.test_evidence_validation import ai_response
from tests.test_openai_provider import request_context


def events(item_type: str = "agent_message") -> str:
    return "\n".join(
        json.dumps(value)
        for value in [
            {"type": "thread.started", "thread_id": "thread-test"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": item_type, "text": "private text"}},
            {"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 45}},
        ]
    )


def test_codex_evaluation_supplies_only_request_in_isolated_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = request_context()
    calls: list[list[str]] = []

    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.167.1\n", "")
        assert command[:2] == ["codex", "exec"]
        assert command[-1] == "-"
        assert "--ignore-user-config" in command
        assert "--ignore-rules" not in command
        assert "--ephemeral" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--model") + 1] == "test-model"
        assert 'model_reasoning_effort="medium"' in command
        assert 'web_search="disabled"' in command
        for feature in ["shell_tool", "apps", "plugins", "skill_search", "multi_agent", "memories"]:
            assert command[command.index(feature) - 1] == "--disable"
        cwd = Path(command[command.index("-C") + 1])
        assert cwd.is_dir() and list(cwd.iterdir()) == []
        assert kwargs["cwd"] == cwd
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assessment = schema["$defs"]["DiagnosisAssessment"]
        assert set(assessment["required"]) == set(assessment["properties"])
        prompt = kwargs["input"]
        assert isinstance(prompt, str)
        assert prompt.startswith(AI_DIAGNOSIS_INSTRUCTIONS)
        assert prompt.endswith(json.dumps(request.to_json_value(), ensure_ascii=False))
        assert "Do not use tools" in prompt
        assert kwargs["timeout"] == 90
        Path(command[command.index("-o") + 1]).write_text(ai_response().model_dump_json())
        return subprocess.CompletedProcess(command, 0, events(), "ignored private stderr")

    monkeypatch.setattr(subprocess, "run", invoke)
    provider = CodexEvaluationProvider(model="test-model", timeout_seconds=90)
    result = provider.diagnose(request)
    assert result.diagnosis == ai_response()
    assert result.metadata.model == "test-model"
    assert result.metadata.prompt_version == AI_DIAGNOSIS_PROMPT_VERSION
    assert result.metadata.request_id == "thread-test"
    assert (result.metadata.input_tokens, result.metadata.output_tokens) == (123, 45)
    assert provider.cli_version == "codex-cli 0.167.1"
    assert len(calls) == 2
    assert not Path(calls[-1][calls[-1].index("-C") + 1]).exists()


@pytest.mark.parametrize(
    "item_type",
    ["command_execution", "mcp_tool_call", "web_search", "file_change", "todo_list", "unknown"],
)
def test_codex_evaluation_rejects_tool_or_unknown_items(
    monkeypatch: pytest.MonkeyPatch,
    item_type: str,
) -> None:
    provider = CodexEvaluationProvider(model="test-model")
    provider.cli_version = "codex-cli test"
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                ["codex"],
                0,
                events(item_type),
                "private stderr",
            )
        ),
    )
    with pytest.raises(LLMProviderError, match="tool use or unsupported item") as error:
        provider.diagnose(request_context())
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "timeout",
        "exit",
        "invalid_json",
        "invalid_schema",
        "invalid_events",
        "incomplete",
    ],
)
def test_codex_evaluation_failures_are_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    provider = CodexEvaluationProvider(model="test-model", timeout_seconds=1)
    provider.cli_version = "codex-cli test"

    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 1
        if failure == "missing":
            raise FileNotFoundError("private details")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output="private details")
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            "not JSON private details"
            if failure == "invalid_json"
            else '{"secret":"private details"}'
        )
        stdout = "private malformed events" if failure == "invalid_events" else events()
        if failure == "incomplete":
            stdout = '{"type":"turn.started"}'
        return subprocess.CompletedProcess(
            command, 1 if failure == "exit" else 0, stdout, "private stderr"
        )

    monkeypatch.setattr(subprocess, "run", invoke)
    with pytest.raises(LLMProviderError) as error:
        provider.diagnose(request_context())
    assert "private" not in str(error.value)
