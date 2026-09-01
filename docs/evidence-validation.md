# AI Evidence Validation

An LLM response is not an effective DagSentry Diagnosis until it passes application-side Evidence
Validation. The AI orchestration layer runs this validation before exposing an AI result, so a
caller cannot accidentally skip it.

## Validation rules

Every AI response must satisfy all of these rules:

- The response conforms to the configured JSON Schema and classification Enum.
- At least one Evidence item supports the Root Cause.
- Every Evidence `line_id` exists in the exact Relevant Log Excerpt sent to the Provider.
- Every Evidence `text` exactly matches the corresponding sanitized excerpt line.
- Reapplying the configured Secret masker does not alter Root Cause, Evidence, or recommended
  actions.

Unknown lines, modified text, missing evidence, or Secret re-exposure set `validation_status` to
`REJECTED`. Before persistence, the entire string-bearing AI output is masked again; raw
re-exposed Secret values are never stored.

## Persistence and fallback

A passed response is stored as one `AI` Diagnosis with `validation_status=PASSED`. A rejected
response and its `RULE` fallback are stored in one database transaction:

- The rejected AI row records only sanitized content and stable `validation_errors` codes.
- The Rule row is the effective fallback Diagnosis.
- `operator_review_required` is retained for valid structured AI output.
- Reuse lookup continues to select only `PASSED` rows, so rejected AI content cannot become a
  reused Diagnosis.

Malformed JSON or invalid Enum values are rejected at the Provider boundary and become a Rule
fallback without materializing unsafe raw response content.
