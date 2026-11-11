#!/usr/bin/env python3
"""Collect real-provider outputs for synthetic cases; semantic verdicts require review."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dagsentry.config import Settings
from dagsentry.db import create_session_factory
from dagsentry.evidence_validation import validate_ai_evidence
from dagsentry.llm import (
    AIDiagnosisRequest,
    ExcerptLineContext,
    FailureMetadata,
    LLMProviderError,
    LogContext,
    RuleDiagnosisContext,
)
from dagsentry.log_processing import LogProcessor
from dagsentry.rule_diagnosis import RuleDiagnosisInput, RuleEngine
from dagsentry.runtime_connections import ConfiguredLLMConnectionResolver


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/diagnosis/cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--live", action="store_true", help="Call the configured provider; may incur charges"
    )
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    if not args.live:
        args.output.write_text(
            json.dumps(
                {
                    "status": "NOT_RUN",
                    "case_count": len(cases),
                    "reason": "Use --live to collect real-provider responses.",
                },
                indent=2,
            )
        )
        return
    settings = Settings()
    provider = (
        ConfiguredLLMConnectionResolver(settings, create_session_factory(settings.database_url))
        .resolve(settings.environment)
        .provider
    )
    if provider is None:
        raise SystemExit("No LLM provider configured; live evaluation not run.")
    processor = LogProcessor()
    results: list[dict[str, object]] = []
    run = {
        "provider": settings.llm_provider,
        "config_source": settings.llm_config_source,
        "configured_model": settings.llm_model,
        "prompt_version": settings.llm_prompt_version,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_attempts": settings.llm_max_attempts,
        "ollama_max_output_tokens": settings.ollama_max_output_tokens,
    }
    for case in cases:
        excerpt = processor.process(case["log"])
        rule = RuleEngine().diagnose(RuleDiagnosisInput("PythonOperator", excerpt))
        request = AIDiagnosisRequest(
            metadata=FailureMetadata(
                environment="evaluation",
                dag_id="synthetic",
                dag_run_id=case["id"],
                task_id="task",
                map_index=-1,
                try_number=1,
                state="FAILED",
                observed_at=datetime.now(UTC),
                operator_type="PythonOperator",
            ),
            rule_diagnosis=RuleDiagnosisContext(
                classification=rule.classification,
                matched_rule=rule.matched_rule,
                ruleset_version=rule.ruleset_version,
                confidence=rule.confidence,
                confidence_reason=rule.confidence_reason,
                extracted_values=tuple(asdict(value) for value in rule.extracted_values),
                evidence_line_ids=rule.evidence_line_ids,
            ),
            log_context=LogContext(
                scope="synthetic_task_try",
                input_line_count=excerpt.input_line_count,
                excerpt_line_count=len(excerpt.lines),
            ),
            error_signature=None,
            excerpt=tuple(ExcerptLineContext(line.line_id, line.text) for line in excerpt.lines),
        )
        try:
            result = provider.diagnose(request)
        except LLMProviderError as error:
            results.append(
                {
                    "id": case["id"],
                    "error": str(error),
                    "review_criteria": case["review"],
                    "semantic_verdict": "NOT_GRADED_PROVIDER_ERROR",
                }
            )
        else:
            validated = validate_ai_evidence(
                result.diagnosis, request.excerpt, log_processor=processor
            )
            results.append(
                {
                    "id": case["id"],
                    "metadata": asdict(result.metadata),
                    "model_response": validated.response.model_copy(
                        update={
                            "root_cause": processor.mask_secrets(result.diagnosis.root_cause),
                            "classification": result.diagnosis.classification,
                            "retry_decision": result.diagnosis.retry_decision,
                            "operator_review_required": result.diagnosis.operator_review_required,
                        }
                    ).model_dump(mode="json"),
                    "response": validated.response.model_dump(mode="json"),
                    "validation_status": validated.status.value,
                    "validation_errors": [item.as_storage_value() for item in validated.rejections],
                    "basis_allowed": validated.response.assessment.basis in case["allowed_basis"],
                    "review_criteria": case["review"],
                    "semantic_verdict": "PENDING_HUMAN_REVIEW",
                }
            )
        args.output.write_text(
            json.dumps(
                {
                    "status": "COLLECTED_NOT_GRADED",
                    "run": run,
                    "response_storage": "Narratives are secret-masked in both response forms.",
                    "case_count": len(cases),
                    "attempted_cases": len(results),
                    "provider_error_cases": sum("error" in item for item in results),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
