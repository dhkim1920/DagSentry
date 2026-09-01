# Airflow Task Log Collection

DagSentry reads logs through the Airflow 3 Public REST API. It does not query the Airflow
Metadata DB.

## Configuration

Select exactly one Airflow configuration source. The default preserves environment-backed
configuration:

```shell
DAGSENTRY_AIRFLOW_CONFIG_SOURCE=environment
DAGSENTRY_AIRFLOW_API_BASE_URL=http://airflow-api-server:8080
DAGSENTRY_AIRFLOW_API_TOKEN=replace-with-an-airflow-bearer-token
```

The token must be allowed to read DAG task logs. It is sent as an HTTP Bearer token and is
redacted from configuration representations.

The default safety limits can be overridden when necessary:

```shell
DAGSENTRY_AIRFLOW_API_TIMEOUT_SECONDS=5
DAGSENTRY_AIRFLOW_API_MAX_ATTEMPTS=2
DAGSENTRY_AIRFLOW_API_RETRY_BACKOFF_SECONDS=0.1
DAGSENTRY_AIRFLOW_LOG_MAX_RESPONSE_BYTES=1048576
```

To use the enabled Airflow Managed Connection matching each Failure Event environment, configure:

```shell
DAGSENTRY_AIRFLOW_CONFIG_SOURCE=database
DAGSENTRY_CONNECTION_ENCRYPTION_KEY=replace-with-the-generated-value
DAGSENTRY_CONNECTION_ENCRYPTION_KEY_VERSION=1
```

In database mode, the Worker reads a fresh immutable connection snapshot when each job starts and
uses its API URL, UI URL, token, timeout, retry, and response-size settings for that job. Connection
updates therefore take effect on the next job without a process restart. A missing or disabled
connection, missing token, invalid config, or decryption failure makes the job fail closed and
retry; environment Airflow values are never used as an implicit fallback.

## Request contract

Airflow 3.2.2 exposes one Task Try log at:

```text
GET /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/logs/{try_number}
    ?map_index={map_index}
    &full_content=false
```

DagSentry requests JSON, follows each `continuation_token` in order, and rejects repeated tokens.
The response-byte limit applies across all pages so a remote log backend cannot cause unbounded
memory use.

The client retries only transport failures, timeouts, HTTP 429, and HTTP 5xx. Authentication,
authorization, missing Task Try, unavailable remote logs, oversized responses, and malformed
responses become a typed `LOG_UNAVAILABLE` result rather than an unhandled exception.

The Diagnosis Worker resolves a stored Failure Event to the exact `dag_id`, `dag_run_id`, `task_id`,
`map_index`, and `try_number`. It does not mark an Outbox job complete merely because raw logs were
fetched.
