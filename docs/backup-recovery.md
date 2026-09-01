# Backup, restore, and emergency Admin recovery

This runbook covers the local users, browser sessions, Admin audit history, and encrypted Managed
Connections stored in PostgreSQL. It does not back up Airflow metadata or external Provider state.

## Security invariants

- A database backup contains password hashes, session and CSRF token hashes, audit history, and
  encrypted Provider Secrets. Treat the backup as sensitive even though it contains no supported
  plaintext credential.
- `DAGSENTRY_CONNECTION_ENCRYPTION_KEY` is never stored in PostgreSQL. Back it up separately in the
  deployment Secret manager together with `DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION`.
- Restore the database and the matching encryption key version as one recovery unit. A database
  restored with the wrong key fails closed when a Managed Connection Secret is used.
- Do not put database passwords, Admin passwords, Provider Secrets, or encryption keys in command
  arguments, shell history, backup filenames, logs, or tickets.
- Do not rotate the Managed Connection encryption key while taking or restoring a backup.

## Backup procedure

Use a restricted PostgreSQL service definition or equivalent workload identity so the database
password does not appear in the process list. The service account needs read access to all
DagSentry tables and sequences. Write backups only to an encrypted, access-controlled location.

1. Record the DagSentry application version, Git revision, Alembic revision, UTC backup time, and
   connection encryption key version. Record only the key version, never the key.
2. Prevent schema migrations and connection-key rotation until the backup and verification finish.
   PostgreSQL `pg_dump` takes a transactionally consistent snapshot, so normal API and Worker
   traffic may continue if the resulting recovery point is acceptable.
3. Set a restrictive file creation mask and create a custom-format logical backup:

   ```shell
   umask 077
   export PGSERVICE=dagsentry-production
   export DAGSENTRY_BACKUP_FILE=/secure/backups/dagsentry-20260822T120000Z.dump
   pg_dump --format=custom --no-owner --no-privileges --file="$DAGSENTRY_BACKUP_FILE"
   pg_restore --list "$DAGSENTRY_BACKUP_FILE"
   sha256sum "$DAGSENTRY_BACKUP_FILE"
   ```

4. Store the checksum and non-secret recovery metadata beside the backup.
5. Confirm separately that the Secret manager retains the matching encryption key and key version.
   Access to the database backup and the encryption key should be granted independently.

The automated PostgreSQL security test serializes every DagSentry table and fails if fixture
passwords, raw session or CSRF tokens, the encryption key, or a Provider Secret appear in the
stored representation. Run it before a release:

```shell
DAGSENTRY_TEST_DATABASE_URL='postgresql+psycopg:///?service=dagsentry-test' \
  uv run pytest tests/integration/test_postgres_security_boundaries.py
```

## Restore verification

Test every backup in an isolated PostgreSQL database before declaring it recoverable. Never point a
verification process at the production database or external Providers.

1. Create an empty isolated database and configure a restricted `dagsentry-restore` PostgreSQL
   service entry for it.
2. Restore the custom-format backup without restoring ownership or grants:

   ```shell
   export PGSERVICE=dagsentry-restore
   export DAGSENTRY_BACKUP_FILE=/secure/backups/dagsentry-20260822T120000Z.dump
   pg_restore --exit-on-error --no-owner --no-privileges \
     --dbname=dagsentry_restore "$DAGSENTRY_BACKUP_FILE"
   ```

3. Supply the backed-up connection encryption key and exact key version through the isolated
   deployment Secret mechanism.
4. Start the same DagSentry version that created the backup and verify:
   - `uv run alembic current` reports the recorded revision;
   - Admin login succeeds and expected users and audit events are present;
   - Managed Connections show only `secret_configured` and their expected metadata;
   - supported read-only connection tests succeed without sending notifications.
5. Apply `uv run alembic upgrade head` only when testing a documented forward upgrade. Re-run the
   application checks after the Migration.
6. Destroy the isolated restore and its temporary Secret copies after verification.

## Production restore

1. Stop DagSentry API, Worker, Recovery Checker, daily report jobs, and Airflow collectors that can
   write to DagSentry.
2. Preserve the failed database and deployment Secrets for investigation; restore into a new empty
   database rather than overwriting the only copy.
3. Restore and verify the backup using the procedure above.
4. Configure the restored database endpoint and its matching encryption key version.
5. Apply only the forward Migrations required by the application version being started.
6. Start the API first and verify readiness and Admin login. Then start the Worker and scheduled
   jobs while watching authentication failures, Outbox backlog, and connection errors.
7. Retain the previous database until the recovery point and all Managed Connections are verified.

Migration downgrade is a development and compatibility check, not the primary production rollback
mechanism. If an application upgrade fails, stop writers and restore the pre-upgrade database
backup together with the previous application image and matching encryption key version.

## Emergency Admin password recovery

Use the recovery CLI from a trusted maintenance shell with access to the production database. The
new password is read twice from a hidden interactive prompt and is never accepted as an argument.

```shell
uv run dagsentry-admin users reset-password admin@example.com
```

The command changes only an existing Admin password, clears `must_change_password`, revokes all of
that Admin's sessions, and appends a `user.password_reset` audit event. After it succeeds:

1. Log in through HTTPS with the new password.
2. Confirm previous browser sessions no longer authenticate.
3. Review recent Admin audit events and revoke other suspicious user sessions.
4. Rotate any external credential that may have been exposed; a password reset does not rotate
   Provider Secrets or the Managed Connection encryption key.

`users bootstrap` works only when the user table is empty. Do not delete users to make bootstrap
available. The reset command does not reactivate a disabled Admin. If no active Admin remains due to
database corruption or unsupported manual changes, keep the service stopped and restore the last
verified backup instead of editing roles directly.

## Lost or mismatched connection encryption key

With a missing or mismatched key, Managed Connection metadata remains readable but Secret use and
decryption fail closed.

- If the correct key exists, restore it with its exact version and restart affected processes.
- If the key is permanently lost, the existing ciphertext is unrecoverable. Generate a new key,
  replace every Managed Connection Secret with newly issued Provider credentials, verify each
  connection, and revoke the old external credentials.
- Do not run key rotation without the current key. Rotation decrypts each existing Secret and
  cannot recover lost key material.

Key rotation after recovery follows [`managed-connections.md`](managed-connections.md#encryption-key-rotation).
