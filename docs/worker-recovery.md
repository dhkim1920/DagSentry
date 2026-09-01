# Worker Recovery

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
