# Failure Reconciler

The Reconciler repairs Failure Events missed when an Airflow Listener, retry callback, or the
DagSentry Ingest API was temporarily unavailable. It uses only Airflow 3 Public REST APIs and sends
recovered failures through the same authenticated Failure Ingest endpoint as real-time collectors.
It never reads the Airflow Metadata DB.

## Scan and watermark contract

Each run fixes its upper boundary to the UTC task start time. It lists current TaskInstances whose
`updated_at` falls within the scan window, follows Airflow pagination, and retrieves the
complete Try history for each exact `dag_id`, `dag_run_id`, `task_id`, and `map_index`. Historical
states `failed` and `up_for_retry` become `RECONCILER` Failure Events; other states are ignored.

The first request uses `limit`, `offset=0`, and `order_by=id`. When the response includes
`next_cursor`, subsequent requests use that cursor; an explicit null ends the scan. Without
that field, offset advances by the number of rows actually received. A short page does not
end the scan: an empty page or a valid nonnegative integer `total_entries` does. Each scan
allows at most 10,000 pages; exceeding the limit fails without advancing the watermark.

The last completely processed upper boundary is stored as an ISO 8601 value in an Airflow Variable.
The default key is `dagsentry_reconciler_watermark_{environment}`. A completed watermark is read
again with a five-minute overlap. On the first run, the default lookback is 24 hours.

The watermark advances only after every page, Try history response, and Ingest request succeeds.
If a run fails after partial delivery, the Airflow task retry or next scheduled run replays the
same window. Failure Ingest's versioned `event_key` makes this at-least-once replay idempotent. If
Airflow cannot persist the Variable, the old watermark causes another safe replay rather than data
loss.

## Deployment

Install DagSentry with the Airflow extra, then copy or mount
`dags/dagsentry_reconciler.py` into the Airflow DAG bundle. The DAG runs every five minutes,
disables catchup, allows only one active run, and retries the reconciliation task twice.

Configure the existing collector and Airflow API settings plus the optional tuning values shown in
`.env.example`. The Airflow API bearer token needs read permission for:

```text
GET /api/v2/dags/~/dagRuns/~/taskInstances
GET /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/tries
```

The DAG task also needs permission to read and write its Airflow Variable watermark. The Ingest
token requires only the existing Failure Event POST endpoint. Use separate secrets for the Airflow
API and DagSentry Ingest API.

## Bounds and failure behavior

- Airflow API calls use a 10-second timeout and at most three attempts by default.
- Requests ask for 100 TaskInstances by default; the server may return fewer.
- HTTP 429, 5xx, network errors, and timeouts receive bounded retries.
- Authentication and other permanent HTTP errors fail the DAG task without advancing the watermark.
- All historical Tries of an updated TaskInstance are replayable; deduplication prevents duplicate
  Failure Events and Outbox jobs.
- Failures older than Airflow's retained TaskInstance history cannot be reconstructed.
