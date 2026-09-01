# Operations

The supported single-host production deployment and its Docker Secret workflow are documented in
[`production-deployment.md`](production-deployment.md).

## Process boundaries

Run PostgreSQL, `dagsentry-api`, `dagsentry-worker`, and `dagsentry-scheduler` as separate service
processes. Schedule `dagsentry-recovery-checker` as a one-shot periodic process. The scheduler runs
reporting from DB-backed Daily Report settings. Install the same DagSentry distribution in Airflow for
the Listener, retry callback, and Reconciler. The Worker and Recovery Checker require
Airflow API and Webhook settings from `.env.example`; the Worker and report may also use the
optional LLM settings. Select Webhook, Slack, Teams, or Discord with
`DAGSENTRY_NOTIFICATION_PROVIDER`; all notification-producing processes use the same selection.

Apply migrations before starting either DagSentry process:

```shell
uv run alembic upgrade head
uv run dagsentry-api
uv run dagsentry-worker
uv run dagsentry-recovery-checker
uv run dagsentry-scheduler
```

Both processes handle normal termination. The API delegates graceful connection draining to
Uvicorn. The Worker stops claiming new jobs on SIGINT/SIGTERM, finishes the owned job, records its
result, and exits. A restarted Worker continues pending jobs and does not repeat an already stored
Diagnosis or delivered Notification.

## Failure inspection

Inspect these tables in order when a job is delayed or failed:

1. `daily_report_schedules` and `scheduler_heartbeats`: enabled settings, applied revision, and
   scheduler liveness.
2. `daily_report_schedule_runs` and `daily_reports`: requested execution, report snapshot, delivery
   status, attempts, and last failure.
3. `failure_events`: normalized Task Try identity and source.
4. `diagnosis_outbox`: `status`, `attempt_count`, `available_at`, lock owner, and `last_error`.
5. `error_signatures`: canonical grouping and fingerprint version.
6. `diagnoses`: source, validation status/errors, Evidence, and reuse reference.
7. `incidents` and `incident_failure_events`: active grouping, state, and linked failures.
8. `incident_state_transitions`: actor, previous/next state, reason, and transition time.
9. `notification_deliveries`: payload snapshot, attempts, status, suppression reason, and last
   failure category.
10. `incident_recovery_notifications`: recovery payload, delivery attempts, and last failure.
11. `operational_metric_counters`: durable, low-cardinality counters exposed through `/metrics`.

Worker `last_error` is bounded JSON containing stage, category, exception type, and a truncated
message. Webhook errors use stable categories such as `AUTHENTICATION`, `RATE_LIMITED`, and
`UNAVAILABLE`. Logs and stored error values intentionally exclude raw Task logs, credentials,
Provider response bodies, and API keys.

`PENDING` Outbox rows wait until `available_at`. A `PROCESSING` lock older than the configured
stale-lock timeout is automatically reclaimed while retry attempts remain. `DEAD` indicates a
permanent error or exhausted attempts and requires operator inspection. Use
`dagsentry-worker-jobs list-dead` and `dagsentry-worker-jobs requeue` as documented in
[`worker-recovery.md`](worker-recovery.md).

Prometheus scraping, metric meanings, dashboard panels, and initial alert thresholds are documented
in [`metrics.md`](metrics.md).
