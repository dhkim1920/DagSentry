# Release notes

## 0.3.0 (in development)

- The Daily Report scheduler recovers interrupted scheduled/manual executions at startup and on
  each poll. `DAGSENTRY_SCHEDULER_STALE_RUN_TIMEOUT_SECONDS` defaults to 900 seconds. PostgreSQL
  row locks protect live executions from recovery, including runs longer than the timeout.
  Recovery reuses stored reports and delivery keys; committed successful deliveries are skipped.
  See [scheduler recovery and deployment](daily-report.md#apscheduler-automation).
- `dagsentry-api --with-scheduler` launches the API and scheduler as separate processes from one
  foreground command. They share environment configuration, stop together on Ctrl+C/SIGTERM, and
  exit with failure if either child exits unexpectedly. Existing individual commands and production
  Compose service boundaries remain available.

- All built-in Diagnosis LLM and Notification Providers support database Managed Connection runtime
  selection and Admin editing. Apply Alembic revision `0021` before using SMTP. Jobs/runs retain
  immutable connection snapshots; database selection never falls back to environment credentials.
  Read-only external tests remain limited to Airflow, Ollama and Slack.
- Provider API v1 is available through `dagsentry.extensions`, with packaged optional pytest
  harnesses and external examples. See [Provider extension API](provider-extension-api.md).
- **Deprecated in 0.3; earliest removal 0.5:** `X-DagSentry-Viewer-Token`,
  `X-DagSentry-Operator-Token`, `DAGSENTRY_VIEWER_API_TOKEN`, `DAGSENTRY_OPERATOR_API_TOKEN`,
  and `DAGSENTRY_OPERATOR_API_IDENTITY`. They remain functional throughout 0.4.
  Configured API processes emit one `legacy_query_auth_deprecated` startup warning; authenticated
  legacy responses carry `X-DagSentry-Auth-Deprecation`. Warnings contain no credential values.

Migrate human users and API clients to individual local users and session authentication:

1. Bootstrap an Admin with the interactive `dagsentry-admin` CLI described in
   [installation](installation.md), then create named users with the required role.
2. Log in through `/ui/` or `POST /api/v1/auth/login`; clients retain session/CSRF cookies and send
   `X-CSRF-Token` on state-changing requests. Never combine session cookies and legacy auth headers.
3. Verify access and audited state changes, stop sending legacy headers, remove their settings from
   deployment configuration, and restart the API.

Airflow's ingestion token (`X-DagSentry-Token`, `DAGSENTRY_INGEST_API_TOKEN`) remains supported.
This deprecation does not remove stored users, sessions, Incidents or audit history.
