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

For local development, start the API and scheduler from one foreground command:

```shell
uv run dagsentry-api --with-scheduler
```

Add `--port 8001` to use another HTTP port; the option applies to the API child only.

The launcher starts one independent API process and one independent scheduler process using the
same Python environment, current directory and `.env`. It starts neither PostgreSQL nor the Worker,
and does not run migrations. Logs from both services remain visible in the terminal. Ctrl+C and
SIGTERM request graceful shutdown of both children, with a shared five-minute drain limit before
forced termination. If either child exits unexpectedly, the launcher stops the other and returns a
nonzero status. It does not restart services automatically.

Production Compose already starts API and scheduler as separate services with
`docker compose -f compose.production.yaml up -d`; retain its existing individual service commands.

The service processes handle normal termination. The API delegates graceful connection draining to
Uvicorn. The Worker stops claiming new jobs on SIGINT/SIGTERM, finishes the owned job, records its
result, and exits. A restarted Worker continues pending jobs and does not repeat an already stored
Diagnosis or delivered Notification.

## Operational overview

Admins can open **Settings → Operational overview** (`/ui/?view=admin&section=operations`).
The session-only `GET /api/v1/admin/operations` returns the same snapshot across all environments.
Use **Refresh** for a new observation; the page does not refresh automatically.

Apply `alembic upgrade head` through revision `0022` before starting the updated API and Worker.
Restart Workers with this version to populate `worker_heartbeats`. Each Worker process records its
own heartbeat every 10 seconds, including while processing a job. The API considers an unstopped
instance online for 30 seconds after its last heartbeat. Configure these with
`DAGSENTRY_WORKER_HEARTBEAT_INTERVAL_SECONDS` and `DAGSENTRY_WORKER_OFFLINE_AFTER_SECONDS`; keep
the API's offline threshold longer than the Workers' heartbeat interval. Normal Worker exit records
its stop immediately; forced exit becomes offline after the threshold. Scheduler status uses its
existing heartbeat and `DAGSENTRY_SCHEDULER_OFFLINE_AFTER_SECONDS`.

No heartbeat history means **No observation**, including Workers running an older version.
Recent completed jobs do not establish process liveness. Heartbeats establish process presence,
not pipeline progress or external connection health. Last success times come from retained completed
Diagnosis jobs and successful report runs; report runs include manual executions.

The queue distinguishes all pending jobs, jobs ready now, delayed retries, processing jobs, stale
processing locks, failed jobs, and completed jobs. Stale locks use the API's
`DAGSENTRY_WORKER_STALE_LOCK_TIMEOUT_SECONDS`; align this with the Worker configuration.
The oldest ready wait measures time since the earliest eligible `available_at`, excluding delayed
retries. On snapshot failure the UI clears previous counts to unknown. It separately checks
`/health/ready` for database connectivity; a successful connectivity check does not verify migrations
or restore queue counts. The overview contains no raw errors, credentials, or machine names.

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
