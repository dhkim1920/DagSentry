# Log Sanitization and Relevant Excerpt

DagSentry never sends a complete raw Task log to a diagnosis provider. Available Task Try logs
pass through one deterministic pipeline:

```text
Secret masking
→ Airflow noise removal
→ relevant evidence selection
→ dynamic-value normalization
→ bounded excerpt and lineId assignment
```

Masking always happens first. Built-in rules cover password, token, API-key, client-secret,
Authorization header, and connection-URI credentials. Deployment-specific values can be masked
with a JSON array of Python regular expressions:

```shell
DAGSENTRY_LOG_SECRET_PATTERNS='["tenant-secret-[A-Za-z0-9]+"]'
```

The complete match is replaced with `[REDACTED]`. Invalid custom expressions fail when the
processor starts instead of silently weakening masking.

The default input limit is 1,048,576 characters. The resulting excerpt contains at most 80 lines
and 16,000 total line characters. Evidence lines keep their original order and use their
one-based raw-log line number as `lineId`. If the character limit cannot fit another complete
older line, that line is omitted; a single oversized evidence line keeps its most recent tail.

Relevant lines include Python exception chains and stack frames, vendor codes such as Oracle and
SQLSTATE errors, explicit failure levels, OOM failures, and HTTP errors. Two surrounding lines are
included by default. When no signal exists, the final ten non-noise lines are retained so sparse
logs remain diagnosable without raising an exception.

Timestamps, UUIDs, labeled numeric IDs, long numeric values, temporary paths, Python stack-frame
line numbers, and hexadecimal addresses are normalized. Masked and normalized excerpt data is the
only log representation exposed to later Rule and AI diagnosis stages; raw logs are not persisted
by the current implementation.
