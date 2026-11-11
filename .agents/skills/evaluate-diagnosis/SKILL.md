---
name: evaluate-diagnosis
description: Re-evaluate DagSentry diagnosis evidence claims when changing models, prompts, or assessment rules.
---

# Evaluate diagnosis evidence

Run from the repository root. Read `evals/diagnosis/cases.json` and `src/dagsentry/prompts.py` first.

1. Run `uv run pytest tests/test_diagnosis_assessment.py tests/test_evidence_validation.py tests/test_ai_diagnosis.py tests/test_diagnosis_assessment_migration.py tests/test_diagnosis_evaluation.py -q`. These are contract and regression tests, not measurements of model accuracy.
2. Run `uv run python scripts/evaluate-diagnosis.py --output /tmp/diagnosis-eval.json --live` with the configured environment or managed LLM connection. The command sends only synthetic cases. It may incur provider charges. Do not print credentials. Without `--live`, the runner records `NOT_RUN` and makes no calls.
3. Also run with `--cases evals/diagnosis/variants.json` and a separate output file. These scenarios check broader behavior but are development fixtures, not a held-out production benchmark. The runner continues after provider errors and counts them in `provider_error_cases`. Never discard failed cases from the denominator. Narratives in both response forms are secret-masked. Compare `model_response` with the effective `response`; runtime retry normalization is not model compliance.
4. Review every response against the case's `review` criteria. Check the actual conclusion, not just its basis label. DIRECT must be limited to facts directly supported by the supplied log. Check that candidate causes remain hypotheses, conflicting evidence is acknowledged, and missing information leads to useful next checks. Validate Korean narratives separately from verbatim evidence. Reject requests for credential originals. Review retry advice for both transient-failure evidence and safe replay; UNKNOWN is expected when either is missing.
5. Record each case as correct, unsupported conclusion, or appropriate abstention. Keep the original response, model, prompt version, and reviewer verdict. Automated citation validation and `basis_allowed` do not constitute semantic correctness.
6. Report counts and denominators for unsupported conclusions, abstentions, and correct substantive diagnoses. Do not call a seven-case synthetic suite production accuracy. Compare changes against saved baseline responses. If a real model was not called, report live evaluation as not run.

For production evaluation, add operator-confirmed causes with sanitized logs. Keep repeated occurrences of one incident/signature together when separating tuning and evaluation data. Never use the same examples both to tune a prompt and claim held-out accuracy. Historical numeric confidence is not a target label.
