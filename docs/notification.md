# Notification delivery

The Diagnosis pipeline delivers provider-neutral payloads through Webhook, Slack, Teams, Discord, or SMTP
adapters. Core code depends on `NotificationProvider`; provider-specific HTTP details remain in the
adapters.

The payload contains the Failure, Diagnosis, and Incident IDs, Incident state and failure count,
environment, DAG/run/task/Try identity,
failure timestamp, classification, Root Cause, confidence, sanitized Evidence, Error Signature,
recommended actions, retry decision, Airflow log link, Diagnosis source, and an explicit
`is_rule_fallback` flag. Rule fallback payloads remain useful when logs or the LLM are unavailable.

## Delivery guarantees

`notification_deliveries` stores one row per effective Diagnosis with:

- a versioned SHA-256 delivery key;
- Provider name and delivery status;
- attempt count and sanitized failure category;
- last HTTP status and delivery timestamp;
- the exact JSON payload snapshot.

For Incident-aware delivery, the initial Failure of a new Incident receives the full Webhook.
One final FAILED event per active Incident receives an additional notification. If the initial
event is already FAILED, it occupies both slots and sends only once. Migration 0019 backfills that
reservation for existing Incidents. A conditional update reserves the final event under concurrent
Workers. Retries keep its delivery key and skip completed diagnosis stages. Later retries are
SUPPRESSED with REPEATED_ACTIVE_INCIDENT; later FAILED events use REPEATED_FINAL_FAILURE.

Teams and SMTP display Korean labels and Asia/Seoul timestamps by default; override with
`DAGSENTRY_DISPLAY_TIMEZONE`. Slack and Discord also distinguish FAILED from UP_FOR_RETRY.
The stored payload includes `failure_state`, but the Webhook adapter excludes this new field to
preserve the existing machine contract, including strict receivers that reject unknown fields.

DagSentry has no time- or count-based reminder policy. Add reminders only after an operational
requirement defines their cadence and reset behavior. Once an Incident is no longer active, a
matching new Failure creates a new Incident and receives a new full notification.

The Webhook request sends the stable key in `Idempotency-Key`. DagSentry locks the delivery row so
concurrent Worker attempts do not call the Provider twice. Reprocessing a delivered Diagnosis is a
database-only no-op. Receivers should also enforce the supplied key because no distributed system
can atomically commit a remote HTTP response and a local database transaction.

Webhook failures never roll back the stored Failure Event or Diagnosis. Timeout, HTTP 408/429,
5xx, and transport errors are retried within configured bounds and then returned to the Outbox
Worker. Authentication, authorization, and other 4xx errors are permanent failures.
