# Operational Metrics

DagSentry exposes Prometheus text metrics from the Ingest API:

```text
GET /metrics
```

Apply database migrations through `0010` before starting any DagSentry service. Counters are stored
in PostgreSQL so the API can expose outcomes produced by separate API, Worker, and Recovery Checker
processes, and process restarts do not reset them. Backlog, processing delay, and current Incident
counts are calculated from current database state when Prometheus scrapes the endpoint.

The endpoint is intentionally unauthenticated for Prometheus compatibility. Restrict it to the
monitoring network at the ingress, service mesh, or firewall layer.

## Metrics

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `dagsentry_ingest_events_total` | Counter | `result` | Created, duplicate, or failed Ingest requests |
| `dagsentry_outbox_jobs` | Gauge | `status` | Current Outbox rows by lifecycle status |
| `dagsentry_outbox_oldest_ready_age_seconds` | Gauge | none | Delay of the oldest ready `PENDING` job |
| `dagsentry_worker_jobs_total` | Counter | `result` | Completed, retried, dead, or stale-reclaimed jobs |
| `dagsentry_log_collection_total` | Counter | `result` | Available or unavailable Airflow logs |
| `dagsentry_diagnosis_outcomes_total` | Counter | `result` | AI success, reuse, Evidence rejection, and Rule fallback reasons |
| `dagsentry_notification_attempts_total` | Counter | `result` | Delivered or failed Diagnosis and Recovery notifications |
| `dagsentry_incident_events_total` | Counter | `event` | Opened and automatically recovered Incidents |
| `dagsentry_incidents` | Gauge | `status` | Current Incidents by lifecycle state |
| `dagsentry_recovery_checker_runs_total` | Counter | `result` | Successful or degraded Checker executions |
| `dagsentry_recovery_checker_delay_seconds` | Gauge | none | Time since the latest completed Checker execution; `NaN` before its first run |

Every label value is enforced by an application allowlist. DAG IDs, Task IDs, environments, UUIDs,
error messages, exception text, and raw Task logs are never metric labels.

## Dashboard baseline

A minimal dashboard should include:

- Ingest created, duplicate, and failure rates over five minutes.
- Outbox jobs by status and oldest-ready age.
- Worker retry, dead, and stale-reclaim increases.
- Log-unavailable ratio and Diagnosis outcomes.
- Notification failure increases.
- Current Incidents by state and opened/recovered increases.
- Recovery Checker delay and degraded runs.

Suggested starting PromQL expressions:

```promql
sum by (result) (rate(dagsentry_ingest_events_total[5m]))
sum by (status) (dagsentry_outbox_jobs)
increase(dagsentry_worker_jobs_total{result="dead"}[5m])
sum(rate(dagsentry_log_collection_total{result="unavailable"}[15m]))
  / clamp_min(sum(rate(dagsentry_log_collection_total[15m])), 0.001)
increase(dagsentry_notification_attempts_total{result="failed"}[5m])
sum by (status) (dagsentry_incidents)
dagsentry_recovery_checker_delay_seconds
```

## Initial alert thresholds

Tune these values after observing normal production traffic:

- Warning when `PENDING` backlog exceeds 100 for 10 minutes.
- Warning when oldest-ready age exceeds 300 seconds for 10 minutes; critical above 900 seconds for
  5 minutes.
- Warning on any increase in `dead` jobs or failed notifications during 5 minutes.
- Warning when log-unavailable ratio exceeds 10% for 15 minutes and at least 20 collections occurred.
- Warning on any `provider_error` or `evidence_rejected` Diagnosis increase during 15 minutes.
- Warning when Recovery Checker delay exceeds 300 seconds or any degraded run occurs. This assumes
  the documented one-minute Checker schedule; use roughly five times the configured schedule.

Counter rates and increases may briefly be unavailable immediately after a new Prometheus target is
added. Database gauge alerts remain usable during that period.
