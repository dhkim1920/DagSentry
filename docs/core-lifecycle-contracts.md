# Core lifecycle contracts

DagSentry persists explicit state Enums and permits state changes only through the owning domain
operation. Failure Events, Diagnoses, and Error Signatures are immutable facts; the Diagnosis
Outbox is the only core record with a retry lifecycle.

## Failure Event

`FailureState` records the Airflow state observed for one failed Try:

- `UP_FOR_RETRY`: accepted from the retry callback or Reconciler.
- `FAILED`: accepted from the final-failure Listener or Reconciler.

The source/state validator rejects a retry callback with `FAILED` and a Listener with
`UP_FOR_RETRY`. Once inserted, a Failure Event never changes state. A later Try is a different
Failure Event identified by its own `try_number` and `event_key`.

## Diagnosis Outbox

`OutboxStatus` has four values and the following allowed transitions:

```text
insert -> PENDING
PENDING -> PROCESSING
PROCESSING -> COMPLETED
PROCESSING -> PENDING       retryable failure with attempts remaining
PROCESSING -> PROCESSING    stale lock reclaimed by a new Worker
PROCESSING -> DEAD          permanent failure or attempts exhausted
DEAD -> PENDING             explicit operator requeue only
```

`COMPLETED` is terminal. A stale final-attempt `PROCESSING` row becomes `DEAD`. Ownership, lock,
and attempt predicates prevent a Worker from finalizing a job it no longer owns. Manual requeue is
restricted to `DEAD`, clears prior lock/error data, and starts a fresh bounded attempt cycle.

## Diagnosis

`DiagnosisSource` is `RULE`, `AI`, or `REUSED`. `DiagnosisValidationStatus` is `PASSED` or
`REJECTED`.

- A Rule Diagnosis is deterministic and stored as `PASSED`.
- Schema- and Evidence-valid AI output is stored as `AI/PASSED`.
- Rejected AI output is stored as `AI/REJECTED`; the effective Rule fallback is a separate row.
- A reused result is stored as `REUSED/PASSED` and references a compatible original.

Diagnosis rows do not transition after insertion. A later attempt or fallback creates another row,
preserving source, validation, Evidence, and version history.

## Error Signature

`SignatureStatus` is `SIGNABLE` or `UNSIGNABLE`. This is an immutable calculation result, not a
mutable database lifecycle:

- `SIGNABLE` requires canonical data and a fingerprint and may be persisted idempotently.
- `UNSIGNABLE` requires all canonical/fingerprint fields to be absent and creates no Signature row.

A future fingerprint rule change creates a new `fingerprint_version`; an existing Signature never
changes status or identity.

## Verification

State/value validation and transition behavior are covered by:

```shell
uv run pytest tests/test_failure_event.py tests/test_worker.py \
  tests/test_diagnosis.py tests/test_error_signature.py
```
