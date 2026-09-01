# Production Docker Compose deployment

Docker Compose on one Linux host is the first supported DagSentry production deployment. This
manifest runs PostgreSQL, one Migration job, the API, and one Diagnosis Worker. Recovery checks and
daily reports remain explicit one-shot jobs for an external scheduler. Kubernetes and multi-host
orchestration are not part of this deployment contract.

## Prerequisites and boundary

- Docker Engine with Docker Compose v2.20 or newer.
- A host firewall and an HTTPS reverse proxy. The API binds to `127.0.0.1` by default; expose only
  the reverse proxy publicly.
- Encrypted, access-controlled storage for Docker volumes, backups, and Secret source files.
- Network reachability from the Worker to the configured Airflow, LLM, and Notification endpoints.

The image runs as UID/GID `10001`, drops Linux capabilities, enables `no-new-privileges`, and uses a
read-only root filesystem with a bounded `/tmp`. PostgreSQL has no published host port. The API and
packaged UI stay on the same origin so session cookies and CSRF protection keep their documented
contract.

## Prepare configuration and Secrets

Create the untracked configuration from the committed example:

```shell
cp deployment/production.env.example deployment/production.env
install -d -m 0700 deployment/secrets
umask 077
```

Review `deployment/production.env`. `DAGSENTRY_ENVIRONMENT=production` is required for Secure
session cookies. The default runtime sources are:

- Airflow: database-backed Managed Connection;
- LLM: environment source with no Provider, which uses deterministic Rule fallback;
- Notification: database-backed Managed Connection.

Generate independent Secret values. The generated hexadecimal PostgreSQL password is URL-safe:

```shell
postgres_password=$(openssl rand -hex 32)
printf '%s\n' "$postgres_password" >deployment/secrets/postgres_password
printf 'postgresql+psycopg://dagsentry:%s@postgres:5432/dagsentry\n' \
  "$postgres_password" >deployment/secrets/database_url
cp deployment/secrets/database_url deployment/secrets/migration_database_url
unset postgres_password
openssl rand -hex 32 >deployment/secrets/ingest_api_token
openssl rand -base64 32 >deployment/secrets/connection_encryption_key
chmod 0600 deployment/secrets/*
```

The Compose manifest mounts each file through Docker secrets. The container entrypoint maps only
files named `DAGSENTRY_*` into process environment variables, rejects empty files, and never prints
their values. The PostgreSQL password uses the image's native `POSTGRES_PASSWORD_FILE` support.
The quick-start copy gives Migration and runtime processes the same database account. For an
externally managed PostgreSQL deployment, replace `migration_database_url` with a schema-owner URL
and `database_url` with a DML-only runtime URL by following
[`security-checklist.md`](security-checklist.md#postgresql-role-separation).

Do not commit `deployment/production.env` or anything under `deployment/secrets/`; both paths are
ignored. In a managed environment, replace the local files with paths populated by the host Secret
manager while retaining the filenames expected by `compose.production.yaml`.

## Build and start

Always pass the production environment file explicitly so Compose interpolation cannot use an
unrelated development `.env`:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml config --quiet
docker compose --env-file deployment/production.env \
  -f compose.production.yaml build
docker compose --env-file deployment/production.env \
  -f compose.production.yaml up -d --wait api worker
```

The API and Worker wait for PostgreSQL readiness and a successful one-shot `alembic upgrade head`.
If Migration fails, neither long-running DagSentry service starts. Check status and readiness:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml ps --all
curl --fail http://127.0.0.1:8000/health/ready
```

Keep the host listener private. Configure the reverse proxy for HTTPS, forward requests to
`127.0.0.1:8000`, preserve `Host`, and do not cache `/api/`, `/metrics`, or `/ui/`. Restrict
`/metrics` at the network boundary.

## Bootstrap and configure

Create the first Admin from an interactive trusted shell:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml run --rm api \
  dagsentry-admin users bootstrap --email admin@example.com --display-name Admin
```

Sign in through the HTTPS URL and create enabled Managed Connections for the exact
`DAGSENTRY_ENVIRONMENT` value:

1. Airflow connection with a read-only Public API token and UI URL.
2. Slack Notification connection with only the scopes documented in `slack-provider.md`.
3. Optionally, an Ollama LLM connection; then change `DAGSENTRY_LLM_CONFIG_SOURCE` to `database`
   and recreate the Worker.

The Airflow Listener, retry callback, and Reconciler run in the Airflow environment rather than in
this Compose stack. Configure their ingest URL to the HTTPS DagSentry endpoint and inject the same
value stored in `deployment/secrets/ingest_api_token` through the Airflow deployment Secret
mechanism.

## Scale and scheduled jobs

The transactional Outbox permits multiple Workers:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml up -d --scale worker=2 worker
```

Use a host scheduler with overlapping-run prevention for the one-shot jobs. Each invocation is
idempotent at the database boundary:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml --profile jobs run --rm recovery-checker
docker compose --env-file deployment/production.env \
  -f compose.production.yaml --profile jobs run --rm daily-report
```

The bundled Airflow DAG may schedule daily reporting instead; do not schedule both paths for the
same date and environment unless testing idempotency intentionally.

## Secret rotation and service recreation

After changing a Docker Secret source file, recreate only the services that consume it. Compose
does not update the environment of an already running container:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml up -d --force-recreate api worker
```

Rotate the ingest token in DagSentry and Airflow collectors as one coordinated change. Managed
Connection credentials are replaced through the Admin UI. Encryption-key rotation requires the
maintenance procedure in [`managed-connections.md`](managed-connections.md#encryption-key-rotation),
not merely replacing the Secret file.

## Upgrade and rollback

Before an upgrade, take and verify the database backup and retain the current image and matching
connection encryption key version. Pause external collectors and scheduled jobs, then stop the API
and Worker so no writer remains active while the schema changes. Build the intended revision and
let the one-shot Migration complete before API or Worker replacement:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml stop api worker
docker compose --env-file deployment/production.env \
  -f compose.production.yaml build
docker compose --env-file deployment/production.env \
  -f compose.production.yaml up -d --wait api worker
```

Do not use Alembic downgrade as the primary production rollback. Restore the pre-upgrade database,
previous image, and matching encryption key as one unit according to
[`backup-recovery.md`](backup-recovery.md#production-restore).

For the current `0.3.x` development line, CI treats the `0.2.x` schema at Alembic revision `0008`
as the supported previous-minor upgrade baseline. The PostgreSQL integration test creates that
schema, inserts a representative Failure, Diagnosis, Notification, and Incident graph, upgrades to
`head`, and verifies both legacy data and newly introduced tables. A current-minor guard makes the
test fail when the package moves beyond `0.3.x`, so the baseline must be reviewed at each minor
version change. Run it locally with:

```shell
DAGSENTRY_TEST_DATABASE_URL="$DAGSENTRY_DATABASE_URL" \
  uv run pytest tests/integration/test_postgres_migrations.py
```

## Verification and shutdown

The repository smoke test creates an isolated Compose project and temporary volume, builds the
production image, applies all Migrations, starts API and Worker, checks readiness, and removes the
test project afterward:

```shell
bash scripts/verify-production-compose.sh
```

To stop production without deleting PostgreSQL data:

```shell
docker compose --env-file deployment/production.env \
  -f compose.production.yaml down
```

Never add `--volumes` to a production shutdown unless permanent database deletion is explicitly
intended and a verified backup exists.
