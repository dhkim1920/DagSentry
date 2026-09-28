# Version compatibility and deprecation policy

This document defines only compatibility that is backed by the repository's executable checks.
An installable dependency range is not, by itself, evidence that every version in that range has
been exercised. DagSentry is currently an untagged `0.3.0.dev0` development line; operators must
identify deployed builds by an immutable image digest or Git commit in addition to the package
version.

## Compatibility status

- **Tested** means the combination runs in CI on every pull request.
- **Declared** means packaging permits the range, while CI exercises the stated boundary versions.
- **Upgrade source** means only a forward database upgrade is tested. The older application binary
  is not maintained or tested as a running service.
- Anything not listed is unsupported rather than implicitly compatible.

| Component | Current compatibility | Evidence and limits |
| --- | --- | --- |
| DagSentry application | `0.3.x` development line | API, Worker, scheduled jobs, Airflow package, and Migration must come from the same build. Mixed-version processes are not tested. |
| Python | `3.11`, `3.12` | Both versions run lint, type checks, Migration, unit, and PostgreSQL tests in CI. Python `<=3.10` and `>=3.13` are unsupported. |
| PostgreSQL | `17` | CI and the production Compose manifest use PostgreSQL 17. SQLite is used for bounded unit tests only and is not a production database. |
| Airflow package | Declared `>=3.1.8,<4` | CI targets exact 3.1.8 and 3.3.1 on Python 3.11, including default lazy listener discovery. These are configured test targets; check the deployed commit's CI result rather than assuming it passed. Running-server E2E remains separate. Airflow 4 is unsupported. |
| Production deployment | Linux container, Docker Compose v2.20+ | One Linux host is supported. Kubernetes, multi-host orchestration, and non-container production installs are not compatibility targets. |
| New database install | Alembic `base -> 0020` | CI applies `upgrade head`, runs the complete PostgreSQL suite, and checks the migration graph and upgrade preservation. The current single head is `0020` (`0020_daily_report_v2.py`); update this row when adding a migration. |
| Previous-minor database upgrade | `0.2.x` schema revision `0008 -> 0.3.x head` | CI loads representative v0.2 Failure, Diagnosis, Notification, and Incident data, upgrades it, and verifies preservation. v0.2 is an upgrade source, not a supported running binary. |
| Packaged Web UI browsers | Chromium regression target; cross-browser certification incomplete | `frontend-browser` runs the packaged UI against a seeded demo with Playwright 1.58.2 / Chromium on PRs and pushes to `main`. Firefox, Safari, real devices and external Provider E2E remain separate verification work. Check the workflow result for the deployed commit. |

The Airflow package range is intentionally described as declared rather than claiming that every
patch release has been run. A failure at either CI boundary blocks a DagSentry release. Supporting a
new Python, PostgreSQL, Airflow major, or deployment target requires executable coverage in the
same change that updates this table.

## Version and upgrade rules

DagSentry uses semantic version identifiers for the application distribution. While the project is
on `0.x`, a minor version may contain incompatible changes, subject to the notice period below.
Patch releases must remain backward compatible within their minor line. After `1.0`, incompatible
changes to a supported public interface require a major version.

Application versions and persisted format versions are separate:

- Alembic revisions define the database schema sequence.
- Failure event keys, Error Signatures, Diagnosis content, delivery keys, and report payloads keep
  their own stored version fields.
- Changing a persisted identity algorithm or payload meaning requires incrementing its specific
  version. Existing rows must not be silently reinterpreted as the new format.

Production upgrades follow these rules:

1. Back up and verify PostgreSQL and retain the current image plus encryption keys.
2. Stop or quiesce all writers before applying a schema-changing upgrade.
3. Apply the new build's forward Migration once.
4. Replace API, Worker, job, and Airflow integration processes with the same DagSentry build.
5. Run readiness and a bounded functional check before resuming normal traffic.

Only the database paths in the compatibility table are supported. Skipping multiple minor
versions, running a newer process against an older schema, and running old and new minor versions
concurrently are unsupported unless a future row explicitly says otherwise. Alembic downgrade is
not a production rollback contract; restore the pre-upgrade database and matching application
build as documented in [backup and recovery](backup-recovery.md).

## Public compatibility surface

The deprecation process applies to operator- or integrator-facing behavior:

- documented HTTP routes, methods, authentication mechanisms, request fields, response fields, and
  status-code meanings;
- documented CLI commands and options;
- documented environment variables, Secret filenames, and production Compose inputs;
- packaged Airflow entry points and configuration;
- persisted payload meanings and schema-version contracts.

The following are not public compatibility promises:

- internal Python modules, classes, and functions not documented as extension interfaces;
- Web UI DOM structure, CSS class names, and bundled JavaScript internals;
- direct queries or writes against DagSentry tables;
- untagged development commits other than the current CI-tested build;
- Provider extension interfaces, whose versioning policy remains a separate v1.0 deliverable.

Adding an optional response field or endpoint is normally backward compatible. Removing or
renaming a field, making an optional input required, changing authentication, narrowing accepted
values, or changing an existing status-code meaning is breaking. New enum values must be called out
because exhaustive clients may need an update even when the wire shape is additive.

## Deprecation lifecycle

A future plan or TODO entry is not an active deprecation. A deprecation starts only when release
notes and this document name the affected surface, replacement, first deprecated version, and
earliest removal version.

The required lifecycle is:

1. **Announce** — document the old and replacement behavior, migration steps, and earliest removal
   version. Secret values must never appear in warnings, logs, or examples.
2. **Warn** — emit an observable, bounded warning at the relevant API, CLI, configuration, or
   startup boundary. Warnings must identify the setting or interface, not credential contents.
3. **Coexist** — keep the deprecated path functional for the minimum notice window and test both
   the old path and replacement.
4. **Remove** — delete the old path only in the announced version, with a release-note entry and a
   failing or replacement-focused regression test.
5. **Retire data safely** — use a forward Migration for stored data. Never require operators to
   edit DagSentry tables manually.

Minimum notice is version based rather than calendar based:

- During `0.x`, a surface deprecated in minor `0.N` remains available throughout `0.(N+1)` and may
  be removed no earlier than `0.(N+2)`.
- From `1.0`, a supported public surface may be removed only in the next major version.
- Patch releases do not remove supported behavior.

An actively exploited security flaw, upstream service shutdown, or legal requirement may force an
earlier change. The release must state the exception, provide the safest available migration path,
and preserve stored data whenever technically possible. Convenience or maintenance cost is not an
exception.

## Active deprecation inventory

There are no active deprecations in `0.3.0.dev0`.

The shared Viewer and Operator header-token settings are legacy migration mechanisms and local
user sessions are their intended replacement. The tokens are not formally deprecated until a
release assigns the notice and removal versions above.
The Airflow ingest token is a service credential and is not part of that planned retirement.

## Release checklist

Every minor or major release must:

1. Update the compatibility table and active deprecation inventory.
2. Update the previous-minor PostgreSQL fixture and version guard.
3. Run fresh-install, supported-upgrade, Python, PostgreSQL, and Airflow boundary checks.
4. Document new deprecations, removals, security exceptions, and operator actions in release notes.
5. Verify that the production manifest uses only combinations listed as supported or declared.
