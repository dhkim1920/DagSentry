---
name: evaluate-diagnosis
description: Re-evaluate DagSentry diagnosis evidence claims when changing models, prompts, or assessment rules.
---

# Evaluate diagnosis evidence

Run from the repository root. Read `evals/diagnosis/cases.json` and `src/dagsentry/prompts.py` first.

1. Run `uv run pytest tests/test_diagnosis_assessment.py tests/test_evidence_validation.py tests/test_ai_diagnosis.py tests/test_diagnosis_assessment_migration.py -q`. These are contract and regression tests, not measurements of model accuracy.
2. Run `uv run python scripts/evaluate-diagnosis.py --output /tmp/diagnosis-eval.json --live` with the configured environment or managed LLM connection. The command sends only synthetic cases. It may incur provider charges. Do not print credentials. Without `--live`, the runner records `NOT_RUN` and makes no calls.
3. Review every response against the case's `review` criteria. Check the actual conclusion, not just its basis label. DIRECT must be limited to facts directly supported by the supplied log. Check that candidate causes remain hypotheses, conflicting evidence is acknowledged, and missing information leads to useful next checks. Validate Korean narratives separately from verbatim evidence.
4. Record each case as correct, unsupported conclusion, or appropriate abstention. Keep the original response, model, prompt version, and reviewer verdict. Automated citation validation and `basis_allowed` do not constitute semantic correctness.
5. Report counts and denominators for unsupported conclusions, abstentions, and correct substantive diagnoses. Do not call a seven-case synthetic suite production accuracy. Compare changes against saved baseline responses. If a real model was not called, report live evaluation as not run.

For production evaluation, add operator-confirmed causes with sanitized logs. Keep repeated occurrences of one incident/signature together when separating tuning and evaluation data. Never use the same examples both to tune a prompt and claim held-out accuracy. Historical numeric confidence is not a target label.
