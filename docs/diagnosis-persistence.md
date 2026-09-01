# Diagnosis persistence and reuse

DagSentry stores completed Rule and AI diagnoses in `diagnoses`. A row records its source,
validation status, classification, confidence and rationale, exact sanitized Evidence lines,
recommended actions, retry decision, and the schema/prompt/rule versions that produced it.

`REUSED` rows do not copy Diagnosis content. They retain the new Failure Event and Error Signature,
copy only compatibility versions, and point directly to the original Rule or AI row through
`reused_from_diagnosis_id`. Reuse lookup excludes `REUSED` rows, so repeated failures do not create
reference chains.

## Reuse contract

The caller supplies a `DiagnosisReusePolicy` before considering an AI call. A stored Diagnosis is
eligible only when all of these conditions hold:

- Its Error Signature ID matches and that Signature has the requested `fingerprint_version`.
- Its `validation_status` is `PASSED`.
- It is an original `RULE` or `AI` row, not another `REUSED` row.
- Its diagnosis schema, prompt, and rule versions exactly match the policy, including `NULL`.
- Its `created_at` is within the caller's explicit `max_age`.

An `UNSIGNABLE` failure has no Error Signature ID and is never reused. `REJECTED`, stale, or
version-incompatible rows are also ignored. When more than one compatible original exists, the
newest one is selected.

Call `reuse_diagnosis` before invoking an LLM Provider. A non-null result means the current failure
was resolved by reference and no provider call is needed; a null result allows the pipeline to
continue to AI Diagnosis.

## Operator query API

Diagnosis History accepts either the read-only `X-DagSentry-Viewer-Token` or the
`X-DagSentry-Operator-Token` used by the Incident API:

```text
GET /api/v1/diagnoses
GET /api/v1/diagnoses/{diagnosis_id}
```

The list is offset-paginated with a maximum `limit` of 100. It supports `source`,
`validation_status`, resolved `classification`, `error_signature_id`, `failure_event_id`, and
inclusive UTC `date_from`/`date_to` filters. Results can be stably sorted by `created_at` or
resolved `confidence` in ascending or descending order. `REUSED` rows are filtered and displayed
using their direct original's content without changing their stored source or ID.

Every row identifies `effective_diagnosis_id`; `effective` is true only for that exact row. A
rejected AI attempt therefore remains visible with its validation errors while linking to the Rule
fallback selected for the same Failure. Delivery state is authoritative, with the latest `PASSED`
Diagnosis used only before a delivery snapshot exists.

Detail responses include the exact sanitized Evidence, extracted values, recommendations, Failure
Try, Incident ID, Error Signature, and the effective delivery's Airflow log URL. Raw logs, Provider
responses, and credentials are not exposed.
