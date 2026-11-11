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
    results = []
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
            error_signature=None,
            excerpt=tuple(ExcerptLineContext(line.line_id, line.text) for line in excerpt.lines),
        )
        result = provider.diagnose(request)
        validated = validate_ai_evidence(result.diagnosis, request.excerpt, log_processor=processor)
        results.append(
            {
                "id": case["id"],
                "metadata": asdict(result.metadata),
                "response": validated.response.model_dump(mode="json"),
                "validation_status": validated.status.value,
                "basis_allowed": validated.response.assessment.basis in case["allowed_basis"],
                "review_criteria": case["review"],
                "semantic_verdict": "PENDING_HUMAN_REVIEW",
            }
        )
        args.output.write_text(
            json.dumps(
                {"status": "COLLECTED_NOT_GRADED", "results": results}, ensure_ascii=False, indent=2
            )
        )


if __name__ == "__main__":
    main()
