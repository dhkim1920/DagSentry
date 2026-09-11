# Incident correlation and lifecycle

DagSentry correlates each successfully diagnosed Failure Event after Diagnosis persistence and
before Notification delivery. The initial v0.2 correlation identity is:

```text
environment + dag_id + task_id + error_signature_id
```

Only `OPEN` and `ACKNOWLEDGED` Incidents are active correlation targets. A new matching Failure is
linked to the active Incident; after it becomes `RECOVERED`, `RESOLVED`, or `IGNORED`, a later
Failure creates a new Incident. A database partial unique index guarantees at most one active
Incident per identity, including when multiple Workers correlate failures concurrently. Each
Failure Event can belong to exactly one Incident.

An `UNSIGNABLE` Failure has no reliable grouping key. DagSentry therefore creates a separate
Incident for each such Failure rather than risk combining unrelated outages. Retrying correlation
for the same Failure remains idempotent.

## Lifecycle

The supported states are `OPEN`, `ACKNOWLEDGED`, `RECOVERED`, `RESOLVED`, and `IGNORED`.

- `OPEN` may move to any other state.
- `ACKNOWLEDGED` may move to `RECOVERED`, `RESOLVED`, or `IGNORED`.
- `RECOVERED` may move to `RESOLVED` or `IGNORED`.
- `RESOLVED` and `IGNORED` are terminal.

Acknowledgement, resolution, and ignore decisions require an operator. The system may mark an
active Incident as recovered. AI cannot change Incident state and is explicitly prevented from
resolving an Incident. Repeating the current state is an idempotent no-op.

Migration 0006 does not backfill Incidents for Failure Events processed before it was applied.

## Notification policy

The Incident stores initial_failure_event_id and final_failure_event_id. The first failure and
one final FAILED event receive notifications; an initial FAILED occupies both slots and sends
once. Later failures are suppressed. The final reservation is atomic and survives Provider errors
and Worker restarts. Incident detail exposes is_initial_failure and is_final_failure on each
failure, displayed in Korean or English by the UI. No periodic or count-based reminders are added.

## Operator API

Local Viewer, Operator, and Admin sessions may use these endpoints. For migration compatibility,
you may also configure a token distinct from the Airflow ingest token:

```shell
DAGSENTRY_VIEWER_API_TOKEN=replace-with-a-read-only-random-value
DAGSENTRY_OPERATOR_API_TOKEN=replace-with-a-separate-long-random-value
DAGSENTRY_OPERATOR_API_IDENTITY=oncall@example.com
```

Send the Viewer token as `X-DagSentry-Viewer-Token` or the Operator token as
`X-DagSentry-Operator-Token`. Local sessions use an HttpOnly cookie and a CSRF header for PATCH.
All roles may use GET endpoints; Operator and Admin users may PATCH:

```text
GET   /api/v1/incidents
GET   /api/v1/incidents/{incident_id}
PATCH /api/v1/incidents/{incident_id}/status
```

The list endpoint supports `status`, `environment`, `dag_id`, `task_id`, `limit`, and `offset`.
It also accepts an allowlisted `sort` value (`last_failure_at`, `failure_count`, `created_at`, or
`updated_at`) and `order` (`asc` or `desc`). The default is `last_failure_at desc`; Incident ID is a
stable tie-breaker for repeatable offset pages. Unsupported sort values return HTTP 422.

Each Incident summary includes `failure_count`, `first_failure_at`, and `last_failure_at`, computed
from its linked Failure Events. `Incident.updated_at` remains the latest lifecycle-state change and
is intentionally not overloaded with Failure activity. Detail responses use the same summary and
include linked Failure Events plus the complete transition history. Each Failure includes its
Diagnosis attempts, related Error Signature, and the Airflow log URL captured in the effective
Diagnosis delivery payload when available. Exactly one validated Diagnosis is marked `effective`;
a delivery snapshot is authoritative, with the latest `PASSED` Diagnosis used while delivery has
not yet been created. Rejected attempts remain visible but are never effective.

`REUSED` rows retain their own ID and source while `content_diagnosis_id` and
`reused_from_diagnosis_id` identify the original Diagnosis. The response resolves the original's
sanitized Evidence and Diagnosis content for display without copying it in storage. Detail
responses do not expose raw Airflow logs or Notification Provider responses.

Operator updates accept only `ACKNOWLEDGED`, `RESOLVED`, or `IGNORED`. The request must include
`expected_status`; `reason` is optional and browser-supplied `actor` is rejected. The audit actor is
derived from the logged-in user's email, or from `DAGSENTRY_OPERATOR_API_IDENTITY` for a legacy
Operator token. If another operator changed the
Incident after it was read, the API returns `409 Conflict` instead of overwriting that change.
Every actual state change stores the actor, initiator, timestamp, previous state, next state, and
optional reason in `incident_state_transitions` in the same database transaction as the Incident
update.
