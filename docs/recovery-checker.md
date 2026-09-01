# Recovery Checker

The Recovery Checker is a one-shot process that evaluates active Incidents against current Airflow
TaskInstance state. Run it from an external scheduler, such as a Kubernetes CronJob, once per
minute:

```shell
uv run dagsentry-recovery-checker
```

The process uses the same database, Airflow API, and Webhook settings as the API and Diagnosis
Worker. Apply migrations through `0009` before enabling it. The Airflow token needs read access to:

```text
GET /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances
```

The request includes exact `task_id` and `map_index` filters. DagSentry never reads the Airflow
Metadata Database directly.

## Recovery policy

For each `OPEN` or `ACKNOWLEDGED` Incident, DagSentry deduplicates the TaskInstance identities from
all linked Failure Events and queries their current state. It changes the Incident to `RECOVERED`
only when every response is `SUCCESS`.

- `RUNNING`, `FAILED`, queued, deferred, and null states leave the Incident unchanged.
- Any Airflow timeout, transport error, HTTP error, missing task, or invalid response leaves the
  Incident unchanged. The Airflow client performs bounded retries, and the next scheduled execution
  checks it again.
- `RESOLVED` and `IGNORED` Incidents are never selected or changed automatically.

The transition and a pending recovery notification are stored in one database transaction. The
Webhook receives a stable `Idempotency-Key`. If delivery fails, the Incident remains `RECOVERED`
and the stored notification is retried by the next execution without creating another transition.

The command prints a JSON summary. It exits non-zero when an Airflow lookup or notification delivery
fails so the scheduler can record the degraded run.
