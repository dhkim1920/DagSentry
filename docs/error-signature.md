# Error Signature

DagSentry groups equivalent failures with a deterministic Error Signature. AI never creates or
changes this identity.

## Fingerprint v1

Fingerprint version 1 uses exactly these fields:

```text
operator_type
exception_class
vendor_error_code
normalized_message
application_stack_frame
```

Values are selected only from the already sanitized Relevant Log Excerpt. Leading, trailing, and
repeated whitespace is collapsed; vendor codes are uppercased; missing values remain explicit JSON
`null`. All five keys are serialized as compact UTF-8 JSON with lexicographically sorted keys.

```text
fingerprint = SHA256(canonical_json UTF-8 bytes)
```

The fingerprint is stored with `fingerprint_version = 1`. Any future change to field selection,
normalization, JSON serialization, or hashing must introduce a new version rather than modifying
v1 behavior. A fixed canonical JSON and SHA-256 fixture protects this contract in tests.

## Field selection and UNSIGNABLE

The latest exception class, vendor code, and application stack frame are used. The normalized
message is the latest excerpt line containing that exception or vendor code, otherwise the latest
explicit error, failure, OOM, or HTTP error line.

An operator name or stack frame alone is not enough to group failures. When no exception class,
vendor code, or stable error message exists, the result is `UNSIGNABLE` and no database row is
created. This prevents unrelated low-information failures from collapsing into one shared
Signature.

## Persistence

The database enforces uniqueness on `(fingerprint_version, fingerprint)`. PostgreSQL and SQLite
inserts use conflict-safe creation, so concurrent workers receive the same Signature ID and only
one record remains.

## Operator query API

Error Signature exploration accepts either the read-only `X-DagSentry-Viewer-Token` or the
`X-DagSentry-Operator-Token` used by the Incident API:

```text
GET /api/v1/error-signatures
GET /api/v1/error-signatures/{signature_id}
GET /api/v1/error-signatures/{signature_id}/occurrences
GET /api/v1/error-signatures/{signature_id}/trend
```

The list is offset-paginated with a maximum `limit` of 100. It supports `environment`, `dag_id`,
`task_id`, `classification`, case-insensitive `q`, and inclusive UTC `date_from`/`date_to` filters.
Allowlisted sort fields are `last_seen_at`, `failure_count`, `incident_count`, and `created_at` with
`asc` or `desc` order and Signature ID as the stable tie-breaker. Statistics count exact linked
Failure Events and distinct Incidents after applying the occurrence filters.

Detail returns the canonical fingerprint fields, occurrence statistics, and the latest validated
non-`REUSED` Diagnosis summary when available. Occurrences are a separately bounded resource that
links each Failure Try to its Incident. The trend endpoint requires `date_from` and `date_to`,
accepts at most 366 inclusive UTC days, and returns zero-filled daily buckets. These endpoints do
not expose raw Task logs, Provider responses, or credentials.
