# Worker Recovery

Retry scheduling logs WARNING and DEAD transitions log ERROR. Logs contain safe identifiers
and exception types, never exception messages or database tracebacks. Outbox error metadata retains
the stage/category/type and a fixed message instead of copying the original exception text.

DagSentry automatically recovers Diagnosis Outbox work left in `PROCESSING` when a Worker exits
without finalizing it. The default stale-lock timeout is 900 seconds and can be changed with
`DAGSENTRY_WORKER_STALE_LOCK_TIMEOUT_SECONDS`.

Choose a timeout longer than the maximum expected duration of one complete Diagnosis pipeline.
Once a lock expires, another Worker atomically takes ownership and increments `attempt_count`. The
original Worker can no longer finalize that attempt because completion and failure updates verify
the lock owner and attempt number.

A stale job that already reached `DAGSENTRY_WORKER_MAX_ATTEMPTS` moves to `DEAD` instead of being
claimed again. A retryable poison job is similarly bounded by the maximum attempt count, so later
jobs continue after it becomes `DEAD`.

## Inspect and requeue DEAD jobs

Admins can open **Settings → Failed diagnosis jobs** in the Web UI, filter by environment,
and expand **Review and requeue** to inspect the TaskInstance and Failure Event identifiers.
The list shows the processing attempt count, last failure time, normalized failure category,
stage and exception type; stored error messages and Task logs are not exposed. Correct the cause,
then choose **Requeue job**. This queues processing by a running Worker rather than running the
pipeline in the API request. Refresh the list to check whether processing fails again.

The session-only Admin API is `GET /api/v1/admin/worker-jobs` (bounded pagination with
`environment`, `limit`, and `offset`) and `POST /api/v1/admin/worker-jobs/{outbox_id}/requeue`.
POST requires the session CSRF token and returns `202 Accepted`. Non-DEAD state conflicts return
`409`; missing jobs return `404`. Requeue and the `worker_job.requeued` Admin audit event commit
atomically. Duplicate or concurrent requests cannot reset an already requeued job or add a second
success audit event. Viewer and Operator accounts cannot access these operations.

List up to 100 failed jobs as newline-delimited JSON:

```shell
uv run dagsentry-worker-jobs list-dead --limit 100
```

After inspecting and correcting the underlying problem, requeue exactly one job by either ID:

```shell
uv run dagsentry-worker-jobs requeue --outbox-id <outbox-uuid>
uv run dagsentry-worker-jobs requeue --failure-event-id <failure-event-uuid>
```

Only `DEAD` jobs can be requeued. Requeueing resets `attempt_count` to zero, clears the previous
error and lock fields, and makes the job immediately available for a fresh bounded retry cycle. It
does not delete the Failure Event or any previously stored Diagnosis and Notification records.
