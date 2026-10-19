# Retention and scheduled maintenance

## Preview and apply retention

Run `dagsentry-retention` with the same database and Worker offline threshold as the API.
The number of days is required; no cleanup runs automatically on API/Worker startup.

```shell
uv run dagsentry-retention --days 30
uv run dagsentry-retention --days 30 --batch-size 1000 --apply
```

Without `--apply`, JSON reports a preview and does not delete anything. With `--apply`, counts
represent rows actually deleted in one transaction. Each invocation considers at most `--batch-size`
rows per table (default 1000, maximum 10000). Run again to drain a larger backlog. Concurrent
PostgreSQL invocations skip rows locked by other transactions; a preview is not a reservation.

The cutoff is strictly older than the requested number of days:

- Browser sessions are eligible after expiry or revocation, plus the specified retention period.
  Active sessions, users, and Admin audit history remain intact.
- Worker heartbeat records are eligible after their last heartbeat is older than both the retention
  period and the configured offline threshold. This includes crashed processes without a stop
  marker. The latest observation is always kept, so an offline fleet remains observable after cleanup.
  A previously inactive Worker that resumes heartbeating can recreate its process record.

Failure Events, Diagnosis Outbox rows (including COMPLETED/DEAD), Diagnoses, Incidents, human
diagnoses, delivery records, reports, schedule runs, connection settings, and metric counters remain
intact. These records participate in reuse, audit, or duplicate-delivery protection. This command
does not implement deletion of domain history or rotation of backup archives.

Apply migrations through `0022` before using this command. A maintenance database role needs SELECT
and DELETE on `user_sessions` and `worker_heartbeats`, plus UPDATE on each table's primary-key
column (`id` and `instance_id`, respectively) for PostgreSQL row locks. The command never updates
those keys and does not need schema ownership.

## Linux systemd timers

The optional templates in [`deployment/systemd`](../deployment/systemd/) run a backup at 02:00 UTC
and one retention batch at 04:00 UTC, with up to five minutes of jitter. They are independent jobs;
retention does not assume a backup succeeded. Neither timer is enabled by installing DagSentry.
They use a host installation at `/opt/dagsentry/.venv` and a `dagsentry` Unix account, not Docker
Compose's private PostgreSQL network. Adjust paths, user and database connectivity for the host.

1. Install the same DagSentry build as production at `/opt/dagsentry/.venv`. Install PostgreSQL
   `psql`, `pg_dump`, and `pg_restore` on PATH, with a client version compatible with the server.
2. Install a private `/etc/dagsentry/maintenance.env` from
   [`maintenance.env.example`](../deployment/systemd/maintenance.env.example). Set an actual deployed
   Git commit, connection encryption key version and retention period. Configure libpq service and
   password files for separate restricted backup and maintenance identities. Do not add the encryption
   key itself; backup only needs its version. The service account must be able to read these files.
3. Place `/var/lib/dagsentry/backups` on encrypted, access-controlled storage with enough capacity.
   The unit creates a mode-0700 directory; encryption and off-host replication are operator duties.
4. Copy the four `.service`/`.timer` files to `/etc/systemd/system/` and run:

   ```shell
   sudo systemctl daemon-reload
   sudo systemctl start dagsentry-backup.service
   sudo journalctl -u dagsentry-backup.service
   ```

5. Verify an isolated restore using [Backup and recovery](backup-recovery.md#restore-verification).
   Preview retention from the maintenance shell before enabling its destructive service.
6. Enable the timers and inspect their next execution:

   ```shell
   sudo systemctl enable --now dagsentry-backup.timer dagsentry-retention.timer
   systemctl list-timers 'dagsentry-*'
   ```

Units prohibit overlapping executions of the same service and apply restrictive file permissions.
`Persistent=true` runs a missed calendar execution after startup. A backup may run for up to two
hours and retention for fifteen minutes. Monitor failures, disk usage and backup age in the host's
existing monitoring system. Abruptly killed backups can leave private `.dagsentry-*` staging
directories; they are incomplete and must not be treated as recoverable archives. Remove such a
directory only after confirming no backup process owns it. Completed archives are never pruned by
these jobs. No timers are installed or activated by the repository's tests.
