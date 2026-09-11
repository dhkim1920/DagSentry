# Airflow 3 Failure Collection

The package declares Airflow `>=3.1.8,<4` and registers both `apache_airflow_provider`
and `airflow.plugins` entry points. Compatibility tests separately check provider discovery
and listener registration with default lazy settings on 3.1.8 and 3.3.1. These package tests
do not replace failure collection against a running Airflow server.

DagSentry uses two Airflow public extension points because the listener observes both retryable and
final failures:

- `on_task_instance_failed` sends only TaskInstances whose resulting state is `FAILED`.
- `on_retry_callback` sends the current failed Try whose resulting state is `UP_FOR_RETRY`.

Both paths build the same Failure Ingest payload. The server-side `event_key` remains the final
idempotency boundary if Airflow invokes either path more than once.

## Installation

Install DagSentry with its Airflow dependency in every Airflow image that runs the API server, DAG
processor, scheduler, or task workers:

```shell
pip install 'dagsentry[airflow]'
```

DagSentry publishes an `apache_airflow_provider` entry point. Airflow discovers and registers the
Listener plugin from the installed wheel; no file needs to be copied into `$AIRFLOW_HOME/plugins`.
Verify discovery after installation:

```shell
AIRFLOW__CORE__LAZY_DISCOVER_PROVIDERS=False airflow plugins
```

Set the collector configuration on all those components:

```shell
export DAGSENTRY_ENVIRONMENT=production
export DAGSENTRY_INGEST_URL=http://dagsentry:8000/api/v1/failure-events
export DAGSENTRY_INGEST_API_TOKEN=replace-with-the-server-token
```

## Retry callback policy

Airflow loads cluster policies from `airflow_local_settings.py`. Add the DagSentry helper to the
deployment's existing `task_policy` instead of replacing that policy:

```python
from dagsentry.airflow.policy import install_retry_callback


def task_policy(task):
    # Keep existing organization policy logic here.
    install_retry_callback(task)
```

If a task already has one or more retry callbacks, DagSentry preserves their order and appends its
callback. Applying the policy more than once does not add duplicates.

## Delivery behavior

- Collection uses only public TaskInstance fields: DAG ID, run ID, task ID, map index, Try number,
  end time, and operator type.
- Each HTTP request has a two-second timeout and at most two attempts.
- Only connection/timeout errors, HTTP 429, and HTTP 5xx responses are retried.
- Collector errors are logged without propagating into task, listener, or policy execution.
- Callback state changes made through the UI or CLI are not guaranteed to invoke task callbacks;
  the Reconciler repairs missed TaskInstance updates through the Airflow Public REST API.
