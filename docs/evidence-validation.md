# AI Evidence Validation

An LLM response is not an effective DagSentry Diagnosis until it passes application-side Evidence
Validation. The AI orchestration layer runs validation before exposing an AI result.

## Validation rules

Every AI response must satisfy these rules:

- The response conforms to `AIDiagnosisResponse`, including classification and required `assessment`.
- `DIRECT` assessments contain at least one fact. `INFERRED` and `UNDETERMINED` assessments contain
  missing information and next checks.
- A conclusion other than `UNDETERMINED` has at least one valid Evidence item.
- Every supplied Evidence `line_id` exists in the exact excerpt sent to the Provider, and its
  `text` exactly matches the corresponding sanitized line. Abstention does not excuse invalid citations.
- Reapplying the Secret masker does not change Root Cause, Evidence, actions, or assessment text.
- Generated narratives do not match the restricted credential-original request patterns.

Unknown lines, modified text, required-but-missing evidence, Secret re-exposure, or a detected unsafe
credential request set `validation_status=REJECTED`. `UNDETERMINED` may have empty Evidence. The
credential check recognizes limited Korean and English patterns; it is not a general semantic or
prompt-injection detector. Citation consistency does not prove diagnostic accuracy.

Before persistence, all string-bearing response content is masked again. `UNDETERMINED` responses
receive a generic Korean root-cause abstention and `retry_decision=UNKNOWN`. Non-`DIRECT` responses
require operator review. The observed error classification is preserved independently of cause
certainty. Other retry recommendations still need semantic review for transient failure and safe replay.

## Persistence and fallback

A passed response is stored as one `AI/PASSED` Diagnosis. A rejected response and its `RULE` fallback
are stored in one database transaction. The rejected AI row contains sanitized content and stable
`validation_errors`; the Rule row is effective. The historical reuse API excludes rejected rows.
The Worker analyzes each new Task Try using current evidence instead of signature-only reuse.

Malformed JSON or invalid schema fields are rejected at the Provider boundary and become a Rule
fallback without persisting unsafe raw response content.
