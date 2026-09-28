# Installation and local verification

## DagSentry services

Install Python 3.11 or 3.12 and `uv`, then run:

```shell
uv sync
cp .env.example .env
docker compose up -d postgres
uv run alembic upgrade head
uv run dagsentry-admin users bootstrap --email admin@example.com --display-name Admin
uv run dagsentry-api
```

The copied `.env` contains example-only PostgreSQL values. For the isolated local demo database,
use `docker compose -f compose.yaml -f compose.demo.yaml up -d postgres`.

In a second shell, start the Worker after configuring Airflow API and Webhook credentials:

```shell
uv run dagsentry-worker
```

The bootstrap command reads the password twice from the interactive terminal and never accepts it
as an argument or environment variable. Browser users sign in with this local account. Production
sets secure session cookies; use HTTPS, or set `DAGSENTRY_ENVIRONMENT=demo` only for plain-HTTP local
verification. Legacy Viewer and Operator tokens remain available during the migration period.

`GET /health/live` checks the API process and `GET /health/ready` checks PostgreSQL connectivity.

## Airflow integration

Install the package with its Airflow extra in every Airflow environment:

```shell
uv sync --extra airflow
```

Set `DAGSENTRY_ENVIRONMENT`, `DAGSENTRY_INGEST_URL`, and `DAGSENTRY_INGEST_API_TOKEN`. Register
`dagsentry.airflow.policy.install_retry_callback` from the Airflow cluster `task_policy`; the
packaged Airflow Provider discovers the final-failure Listener automatically. Existing retry
callbacks are preserved and run before DagSentry's callback.

The executable scenario DAG is `tests/airflow/dags/failure_scenarios.py`. Load it in a local
Airflow 3 environment and trigger `dagsentry_failure_scenarios` to observe retry recovery,
terminal multi-Try failure, callback/Listener deduplication, and mapped failures. The CI Airflow
compatibility job loads the DAG and tests exact versions `3.1.8` (minimum) and `3.3.1`
(reference) on Python 3.11. It does not automatically track the newest Airflow release.

## Verification

Run all static, unit, PostgreSQL, Migration, and optional Airflow checks:

```shell
bash scripts/verify.sh
```

Webhook receivers should persist the `Idempotency-Key` header and return a 2xx status only after
accepting the payload.
