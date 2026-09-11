# Daily Statistics v2

DagSentry builds daily report inputs with `aggregate_daily_statistics`. Every value is calculated
from PostgreSQL through SQLAlchemy SQL expressions; neither an LLM nor report prose can alter the
numbers.

## Period and scope

One report covers one local calendar day, converted to UTC before querying (including SQLite):

```text
[local report_date midnight, next local midnight), expressed in UTC
```

The report is scoped to one normalized `environment`. A value exactly at the first midnight is
included, while a value at the next midnight belongs to the following report.

## Metric definitions

- `failure_attempts`: Failure Events whose `observed_at` is in the period. Failed retry attempts
  count individually.
- `affected_task_instances`: distinct `dag_id`, `dag_run_id`, `task_id`, and `map_index` identities
  among those Failure Events. Different tries of one mapped TaskInstance count once.
- `affected_dag_runs`: distinct `dag_id` and `dag_run_id` identities.
- `incidents.new`: Incidents created in the period.
- `incidents.unresolved`: Incidents created before the period end that had not reached `RECOVERED`,
  `RESOLVED`, or `IGNORED` by that instant. Later operator changes do not rewrite historical values.
- `incidents.recovered`: distinct Incidents transitioned to `RECOVERED` in the period.
- `error_signatures.new`: Signatures present in the period with no earlier validated Diagnosis in
  the same environment.
- `error_signatures.repeated`: Signatures present in the period that had an earlier validated
  Diagnosis in that environment. Multiple first-day occurrences remain one new Signature.
- `classification_counts`: Failure counts by every `ErrorClassification`. `REUSED` Diagnoses use
  their original Diagnosis classification. A Failure without a validated Diagnosis is not assigned
  a guessed classification, so this sum may be lower than `failure_attempts` while work is pending.
- `mean_time.recovery_seconds`: mean duration from Incident creation to `RECOVERED` for recovery
  transitions in the period.
- `mean_time.resolution_seconds`: mean duration from Incident creation to `RESOLVED` for resolution
  transitions in the period.

An empty period returns zero counts for every classification and `null` mean durations.

## Versioned JSON contract

`top_failures` ranks up to 20 DAG/Task groups by failed event count, then DAG and Task ID.
Classification, Incident, and root cause come from the most recent diagnosed failure; last_failed_at
includes newer failures still awaiting diagnosis. REUSED entries resolve original diagnosis content.

`DailyStatistics` is a frozen Pydantic contract with unknown fields forbidden. Its JSON Schema is
available through `DailyStatistics.model_json_schema()` with `schema_version=2` and an IANA
`timezone`. Consumers must reject unsupported schema versions rather than silently
reinterpreting a field.

Example shape:

```json
{
  "schema_version": 2,
  "report_date": "2026-08-12",
  "timezone": "UTC",
  "period_start": "2026-08-12T00:00:00Z",
  "period_end": "2026-08-13T00:00:00Z",
  "environment": "production",
  "failure_attempts": 3,
  "affected_task_instances": 2,
  "affected_dag_runs": 2,
  "incidents": {"new": 2, "unresolved": 1, "recovered": 1},
  "error_signatures": {"new": 1, "repeated": 1},
  "classification_counts": {
    "DAG_CODE": 1,
    "AIRFLOW_PLATFORM": 0,
    "SOURCE_DATABASE": 0,
    "NETWORK": 2,
    "AUTHENTICATION": 0,
    "AUTHORIZATION": 0,
    "RESOURCE": 0,
    "DATA_QUALITY": 0,
    "EXTERNAL_SYSTEM": 0,
    "CONFIGURATION": 0,
    "UNKNOWN": 0
  },
  "mean_time": {"recovery_seconds": 3600.0, "resolution_seconds": 90000.0}
}
```
