"""Evaluation-only Codex CLI adapter; never registered as a runtime provider."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from dagsentry.llm import (
    AIDiagnosisRequest,
    AIDiagnosisResponse,
    LLMCallMetadata,
    LLMProviderError,
    LLMResult,
)
from dagsentry.prompts import AI_DIAGNOSIS_INSTRUCTIONS, AI_DIAGNOSIS_PROMPT_VERSION


class CodexEvaluationProvider:
    """Run one isolated, structured CLI evaluation per synthetic case."""

    def __init__(
        self, *, model: str, reasoning_effort: str = "medium", timeout_seconds: float = 120
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.cli_version: str | None = None

    def diagnose(self, request: AIDiagnosisRequest) -> LLMResult:
        started = time.monotonic()
        try:
            if self.cli_version is None:
                version = subprocess.run(
                    ["codex", "--version"], capture_output=True, text=True, timeout=5, check=False
                )
                if version.returncode != 0:
                    raise LLMProviderError("Codex CLI version check failed")
                self.cli_version = version.stdout.strip()
            with TemporaryDirectory(prefix="dagsentry-codex-eval-") as directory:
                root = Path(directory)
                cwd = root / "workspace"
                cwd.mkdir()
                schema_path = root / "schema.json"
                output_path = root / "response.json"
                schema = AIDiagnosisResponse.model_json_schema()
                _require_all_properties(schema)
                schema_path.write_text(json.dumps(schema))
                command = [
                    "codex",
                    "exec",
                    "--ignore-user-config",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--json",
                    "--model",
                    self.model,
                    "-c",
                    f'model_reasoning_effort="{self.reasoning_effort}"',
                    "-c",
                    "project_doc_max_bytes=0",
                    "-c",
                    "skills.include_instructions=false",
                    "-c",
                    "skills.bundled.enabled=false",
                    "-c",
                    'web_search="disabled"',
                    *[
                        argument
                        for feature in (
                            "shell_tool",
                            "apps",
                            "plugins",
                            "skill_search",
                            "multi_agent",
                            "browser_use",
                            "computer_use",
                            "image_generation",
                            "view_image",
                            "hooks",
                            "memories",
                        )
                        for argument in ("--disable", feature)
                    ],
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "-C",
                    str(cwd),
                    "-",
                ]
                prompt = (
                    AI_DIAGNOSIS_INSTRUCTIONS
                    + "\nDo not use tools, files, network, skills or external context. "
                    "Return only the diagnosis JSON from the supplied data.\n"
                    + json.dumps(request.to_json_value(), ensure_ascii=False)
                )
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    raise LLMProviderError("Codex CLI execution failed")
                request_id, usage = _validate_events(completed.stdout)
                diagnosis = AIDiagnosisResponse.model_validate_json(output_path.read_text())
        except subprocess.TimeoutExpired:
            raise LLMProviderError("Codex CLI timed out") from None
        except OSError:
            raise LLMProviderError("Codex CLI or response file unavailable") from None
        except ValueError:
            raise LLMProviderError("Codex CLI returned invalid structured output") from None
        return LLMResult(
            diagnosis=diagnosis,
            metadata=LLMCallMetadata(
                request_id=request_id,
                model=self.model,
                prompt_version=AI_DIAGNOSIS_PROMPT_VERSION,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=_token_count(usage.get("input_tokens")),
                output_tokens=_token_count(usage.get("output_tokens")),
            ),
        )


def _require_all_properties(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False
        value.pop("default", None)
        for child in value.values():
            _require_all_properties(child)
    elif isinstance(value, list):
        for child in value:
            _require_all_properties(child)


def _validate_events(stdout: str) -> tuple[str | None, dict[str, object]]:
    request_id = None
    usage: dict[str, object] = {}
    completed = False
    for line in stdout.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise LLMProviderError("Codex CLI returned invalid event stream")
        kind = event.get("type")
        if kind == "thread.started":
            identifier = event.get("thread_id")
            request_id = identifier if isinstance(identifier, str) else None
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") not in {"reasoning", "agent_message"}:
                raise LLMProviderError("Codex evaluation rejected: tool use or unsupported item")
        elif kind == "turn.completed":
            completed = True
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
        elif kind != "turn.started":
            raise LLMProviderError("Codex CLI returned an unsuccessful or unsupported event")
    if not completed:
        raise LLMProviderError("Codex CLI did not complete a turn")
    return request_id, usage


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
