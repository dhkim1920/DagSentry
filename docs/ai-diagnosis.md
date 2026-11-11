# LLM Provider and AI Diagnosis

All five providers use the shared `ai-diagnosis-ko-v5` instructions: root cause and actions are
in Korean, while evidence text preserves the exact original excerpt including whitespace.
Evidence validation checks citation equality, not the correctness of the causal conclusion.

DagSentry's Core depends only on the `LLMProvider` protocol. OpenAI, Azure OpenAI, Anthropic, AWS
Bedrock, and Ollama adapters implement the contract; Provider-specific request fields and response
parsing remain under `dagsentry.providers`.

AI Diagnosis is disabled by default. In that mode, or whenever the Provider fails, the effective
result is the deterministic Rule Diagnosis. An empty Relevant Log Excerpt always skips the
Provider because DagSentry does not ask an LLM to infer a root cause without log evidence.

## Request boundary

The Provider receives a typed allowlist containing only:

- Failure metadata
- Rule Diagnosis
- Error Signature
- sanitized Relevant Log Excerpt lines
- log collection scope and input/excerpt line counts
- an optional previously validated Diagnosis

There is no request field for a full raw Task log or Incident history. The OpenAI adapter also
sets `store` to `false` and requests a strict `json_schema` Structured Output through
`text.format`.

The returned schema contains classification, Root Cause, confidence, Evidence, recommended
actions, retry decision, and whether operator review is required. JSON shape and Enum values are
validated at the Provider boundary. Evidence line identity, exact text, and Secret re-exposure are
then validated before an AI Diagnosis can become `PASSED`; see
[`evidence-validation.md`](evidence-validation.md).

## Configuration

Enable the OpenAI Provider explicitly:

```dotenv
DAGSENTRY_LLM_PROVIDER=openai
DAGSENTRY_LLM_MODEL=gpt-5.6-luna
DAGSENTRY_LLM_PROMPT_VERSION=ai-diagnosis-ko-v5
DAGSENTRY_OPENAI_API_KEY=replace-with-an-openai-api-key
```

Timeout, maximum attempts, retry backoff, and the API base URL are also configurable; see
`.env.example`. HTTP 429, 5xx, timeout, and transport failures are retried only up to the configured
attempt count. Other 4xx responses are not retried.

Successful call logs contain only request ID, model, prompt version, latency, and input/output
token counts. Failure logs contain a sanitized category and never include the API key, request
payload, or response body.

## Evidence assessment

New diagnoses carry a typed `assessment` with evidence basis, reason, observed facts, candidate
causes, missing information, and next checks. UI and notifications show `DIRECT`, `INFERRED`, or
`UNDETERMINED` instead of percentages. These describe support for the stated conclusion, not
measured accuracy or operator confirmation. A 401 proves an authentication rejection, not token
expiration. Memory shortage does not prove a memory leak. Rule assessments limit direct support
to the observed error type.

An `UNDETERMINED` response may have no citations and requires operator review. Invalid supplied
citations and secrets in any assessment narrative still reject the response. The production
pipeline skips AI when no excerpt is available and records an undetermined rule assessment.
Citation validation does not verify the semantic correctness of a cause.

Migration `0023` adds nullable assessment data without backfilling historical rows. Historical
results display as unassessed; reused results display their original assessment. Schema v4 and
ruleset v3 prevent reuse of older compatible-looking results. The legacy `confidence` field
remains in storage and API contracts but does not determine evidence basis.

`evals/diagnosis/cases.json` contains synthetic regression cases. Use the
[diagnosis evaluation skill](../.agents/skills/evaluate-diagnosis/SKILL.md) to collect and review
real-provider results separately from offline contract tests.

The v5 prompt examines all excerpt lines before rule hints and explicitly requires abstention
when conflicting reports cannot be attributed to the failure. It prohibits requesting raw
credentials and requires concrete missing information and next checks for uncertain assessments.
These instructions guide model behavior; citation validation does not guarantee compliance.
For `UNDETERMINED`, the validator sets retry advice to `UNKNOWN` and replaces
the root cause with a Korean statement that the available evidence cannot determine the cause.
Secret checks still inspect the original model text before this normalization.
Other retry advice still requires semantic review of transient failure and safe replay evidence.

The evaluation runner records provider failures per case and continues. It stores secret-masked model output
separately from the effective response so runtime normalization is visible. Evaluate
`evals/diagnosis/variants.json` separately from the original regression cases. Both sets are
synthetic development cases and do not establish production accuracy.

Generated narratives that request credential originals are rejected with
`UNSAFE_CREDENTIAL_REQUEST`. The guard recognizes a bounded set of Korean and English request
patterns, including raw credential values and Authorization header contents. Exact evidence
quotations are excluded, so hostile log text is rejected only when the generated narrative adopts
it. Ambiguous matching narratives are conservatively rejected. This guard is not a comprehensive
prompt-injection detector. Schema v4 prevents reuse of diagnoses accepted before this policy.

Classification describes the observed error family independently of cause assessment. For example,
`RESOURCE` can accompany an `UNDETERMINED` underlying cause after an observed OOM kill. Schema v4
preserves that distinction; historical diagnoses are not reinterpreted. Each new Task Try is
analyzed from its current logs even when the signature matches an earlier failure. This increases
model calls for repeated failures; duplicate processing of the same event remains idempotent.

The excerpt retains supplied timestamps, trace/run identifiers, resource byte values, and stack
line numbers. Structured Airflow logs also retain timestamps and available frame source lines;
frame locals are excluded. Home and temporary paths remain redacted for privacy. Log context
reports the requested Task Try scope and input/excerpt line counts; omitted lines may include
noise and context outside the configured bounds. Counts do not prove remote log completeness.
External resource metrics, repository code, and recent deployments are not automatically collected.
Use `evals/diagnosis/context.json` to review contextual evidence separately from the other suites.
