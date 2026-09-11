# Rule Diagnosis

Ruleset v2 keeps the existing patterns and priority order and adds Korean recommended actions
and advisory retry decisions. Authentication, authorization, memory, and application-code rules
recommend fixing the condition before retrying; ORA-12541 and UNKNOWN retain an UNKNOWN retry
decision. These fields are persisted and included in notifications. Rules do not supply a root cause.

DagSentry runs deterministic rules before any AI diagnosis. Rules reduce sanitized log evidence
to structured information; they do not generate a natural-language Root Cause.

## Result contract

Every result contains:

- a classification from the versioned Core enum;
- the matched rule ID and ruleset version;
- numeric confidence and a reproducible confidence reason;
- extracted name/value pairs;
- evidence `lineId` values that exist in the Relevant Log Excerpt.

When no rule matches, the engine returns `UNKNOWN`, rule `unknown.v1`, confidence `0.0`, and no
evidence. A missing Airflow log therefore remains a normal Rule Diagnosis path instead of stopping
the pipeline. The Diagnosis model persists this result.

## v0.1 rules and conflicts

Rules are evaluated together. The selected match is determined by:

1. higher rule priority;
2. higher (more recent) evidence `lineId` when priorities are equal;
3. lexicographically smaller rule ID as the final deterministic tie-breaker.

| Priority | Rule | Classification |
| ---: | --- | --- |
| 100 | Explicit HTTP 401 | `AUTHENTICATION` |
| 100 | Explicit HTTP 403 | `AUTHORIZATION` |
| 90 | `OOMKilled`, `out of memory`, or `MemoryError` | `RESOURCE` |
| 80 | Oracle `ORA-12541` | `SOURCE_DATABASE` |
| 50 | Python exception with an application stack frame | `DAG_CODE` |

HTTP rules require an explicit HTTP/status expression, so unrelated row counts and port numbers do
not match. The Python rule requires an application frame and therefore does not classify a
library-only traceback as DAG code. Specific infrastructure or external-system evidence wins over
the generic Python exception rule.
