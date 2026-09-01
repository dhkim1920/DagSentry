# Least privilege example and production security checklist

This guide turns the credential boundaries in [security-boundaries.md](security-boundaries.md) into
an operator checklist for the supported single-host Compose deployment. It does not replace the
host, database, identity-provider, or reverse-proxy security baseline maintained by their owners.

## PostgreSQL role separation

The production manifest accepts two database Secret files:

- `migration_database_url` is mounted only into the one-shot Migration container;
- `database_url` is mounted into API, Worker, Recovery Checker, and Daily Report processes.

The quick start copies one owner URL into both files so a new bundled PostgreSQL installation can
bootstrap without an out-of-band role-provisioning step. That is a functional default, not the
least-privilege target. Long-running processes should use a login role with no database or schema
creation rights. A separately controlled PostgreSQL administrator can provision a fresh database
as follows:

```sql
CREATE ROLE dagsentry_migrator
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password dagsentry_migrator

CREATE ROLE dagsentry_runtime
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password dagsentry_runtime

CREATE DATABASE dagsentry OWNER dagsentry_migrator;
REVOKE CONNECT, TEMPORARY ON DATABASE dagsentry FROM PUBLIC;
GRANT CONNECT ON DATABASE dagsentry TO dagsentry_migrator, dagsentry_runtime;

\connect dagsentry
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO dagsentry_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE dagsentry_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dagsentry_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE dagsentry_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO dagsentry_runtime;
```

Run `alembic upgrade head` with the migrator URL. Then have the PostgreSQL administrator cover any
objects created before default privileges were installed:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO dagsentry_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
    TO dagsentry_runtime;
```

Write percent-encoded SQLAlchemy URLs to the untracked Secret files, never to
`deployment/production.env`:

```text
deployment/secrets/migration_database_url  # dagsentry_migrator URL
deployment/secrets/database_url            # dagsentry_runtime URL
```

The runtime role does not need `CREATE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, role membership, or
ownership of any DagSentry object. Verify the negative grants while connected as an administrator:

```sql
SELECT
    has_database_privilege('dagsentry_runtime', 'dagsentry', 'CREATE') AS can_create_database,
    has_database_privilege('dagsentry_runtime', 'dagsentry', 'TEMPORARY') AS can_create_temp,
    has_schema_privilege('dagsentry_runtime', 'public', 'CREATE') AS can_create_in_schema;
```

All three values must be `false`. After every Migration, verify that the runtime role can start the
API and Worker and that new tables received the expected DML grants. Do not grant ownership or DDL
as a shortcut for a missing grant; fix the migrator's default privileges instead.

Backups should use another scoped role with `CONNECT`, schema `USAGE`, and `SELECT` on DagSentry
tables and sequences. Do not reuse the runtime or migrator password for backup storage. The restore
operator may require broader temporary privileges, but that credential must not be mounted into a
running DagSentry service.

## Credential boundary examples

Use a different credential for every environment and purpose. Compromise of a Notification webhook
must not grant access to Airflow, DagSentry ingestion, the database, or an LLM account.

| Caller | Credential | Minimum privilege |
| --- | --- | --- |
| Airflow Listener, retry callback, Reconciler | DagSentry ingest token | Submit Failure Events only. It must not authenticate browser, query, Admin, or database access. |
| DagSentry Worker and Reconciler | Airflow bearer token | Read only the enabled components' DAG runs, TaskInstances, Try history, and bounded Task logs through the Public API. No DAG editing, triggering, connection, variable, or Admin permission. |
| Human Viewer | Local session | Read Incident, Diagnosis, Signature, and report views only. |
| Human Operator | Local session | Viewer access plus documented Incident lifecycle changes. No user or connection administration. |
| Human Admin | Local session | User and Managed Connection administration. Use named accounts; do not share an Admin login. |
| Slack Notification | Bot token | `chat:write` for an invited target channel. Avoid `chat:write.public` unless posting without invitation is explicitly required. |
| Teams or Discord Notification | Dedicated webhook URL | One intended workflow/channel; do not reuse a general automation webhook. Treat the complete URL as a Secret. |
| AWS Bedrock | Workload role or assumed role | `bedrock:InvokeModel` only for configured model or inference-profile resources. No IAM or unrelated Bedrock administration. |
| Other hosted LLM Providers | Project-scoped API key | Restrict project, model, budget, and network source where the Provider supports it. Do not use an account-owner key. |

Managed Connection tests are read-only probes. A successful connection test does not justify write
or Admin permissions at the remote service. Keep Viewer and Operator legacy header tokens unset
for new deployments that use local sessions. The service-to-service ingest token remains required
for Airflow collectors and must be distinct from all human authentication.

## Pre-deployment checklist

- [ ] Pin the DagSentry image by immutable digest or retain the exact Git commit used to build it.
- [ ] Confirm the combination is listed in [version compatibility](version-compatibility.md).
- [ ] Keep the API bound to `127.0.0.1`; expose only an HTTPS reverse proxy through the firewall.
- [ ] Do not publish the PostgreSQL port. Restrict the backend network to the Compose project.
- [ ] Run containers as UID/GID `10001`, with a read-only root filesystem, all capabilities dropped,
      and `no-new-privileges` enabled.
- [ ] Store `deployment/production.env` without credentials and keep every Secret file mode `0600`
      inside an access-controlled directory.
- [ ] Use different random values for PostgreSQL, ingestion, connection encryption, Airflow, LLM,
      and Notification credentials; do not reuse values across staging and production.
- [ ] Mount the schema-owner URL only as `migration_database_url` and the DML-only URL as
      `database_url`.
- [ ] Back up the Managed Connection encryption key separately from PostgreSQL and record its key
      version. Restrict both backup locations.
- [ ] Create a named initial Admin, change any temporary password immediately, and create separate
      Operator and Viewer users.
- [ ] Restrict `/metrics` at the proxy or network boundary and disable proxy caching for `/api/`,
      `/metrics`, and `/ui/`.
- [ ] Set CPU, memory, disk, and log-retention monitoring at the host level. Docker log rotation is
      a bound, not a durable audit archive.

## Deployment verification checklist

- [ ] `docker compose ... config --quiet` succeeds with the intended explicit environment file.
- [ ] Migration completes before API and Worker start; its container receives the migrator Secret,
      while long-running services receive only the runtime database Secret.
- [ ] `GET /health/ready` succeeds only through the intended private listener or HTTPS proxy.
- [ ] Docker inspection confirms user `10001:10001`, read-only root filesystems, dropped
      capabilities, and `no-new-privileges` for every DagSentry process.
- [ ] Docker inspection, application logs, API responses, metrics, and a test database dump contain
      none of the known plaintext Secret fixtures.
- [ ] PostgreSQL privilege checks show no runtime database/schema `CREATE` or `TEMPORARY` access.
- [ ] Viewer cannot mutate Incidents; Operator cannot manage users or connections; Admin actions
      create audit events.
- [ ] Session fixation, cross-session CSRF, and simultaneous cookie/header authentication attempts
      are rejected.
- [ ] Airflow, LLM, and Notification connection tests succeed with their minimum read/send-only
      credentials.
- [ ] A verified backup can be restored into an isolated database with the separately stored
      connection encryption key.

The repository automates the container policy, Secret non-disclosure, PostgreSQL security
boundaries, fresh Migration, previous-minor upgrade, readiness, and UI smoke portions. Remote IAM,
host firewall, reverse proxy TLS, browser behavior, and restored-backup drills remain operator
checks because CI cannot observe those environments.

## Recurring and upgrade checklist

- [ ] Review active Admins, disabled users, sessions, legacy header-token settings, and Admin audit
      events on a scheduled basis.
- [ ] Rotate service and Provider credentials according to their issuer's policy and immediately
      after suspected exposure. Recreate only affected containers after file-backed Secret changes.
- [ ] Test restore and emergency Admin recovery procedures regularly; a successful `pg_dump` alone
      is not a recovery drill.
- [ ] Review Provider permissions after enabling a new feature; do not grant future permissions in
      advance.
- [ ] Before upgrade, verify the supported source version, stop writers, take and verify a backup,
      and retain the previous image and encryption keys.
- [ ] Apply Migration with the migrator credential, restart every DagSentry component on the same
      build, then repeat readiness, privilege, Secret-disclosure, and bounded functional checks.
- [ ] Track deprecations through their announced replacement and removal versions. Do not discover
      a removed setting during production startup.
