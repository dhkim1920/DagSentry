# Web UI

Frontend ownership, module boundaries, and verification are documented in [Frontend architecture](frontend-architecture.md).

DagSentry serves its dependency-free Web UI from the API process. Open `/` to redirect to `/ui/`.
No Node runtime or separate frontend build is required, and the HTML, CSS, and JavaScript assets are
included in the Python wheel.

## Incident Dashboard

The Incident Dashboard provides:

- local account login with server-enforced Viewer, Operator, or Admin permissions;
- default `OPEN` Incident triage;
- status, environment, DAG, and Task filters;
- latest activity, Failure count, creation time, or state update sorting;
- stable offset pagination with 20 rows per page;
- loading, empty, authentication, configuration, and general request error states;
- selectable timezone display with the original UTC time retained, keyboard-visible focus,
  semantic labels and table markup, and responsive narrow layouts.

Filter, sort, order, and offset state are stored in the URL. The opaque session credential is stored
only in a same-origin `HttpOnly`, `SameSite=Strict` cookie and is never exposed to JavaScript,
browser storage, or the URL. State-changing requests send a separate session-bound CSRF header.
**Log out** revokes the database session and expires both browser cookies.

## Incident detail

Select **Inspect** from the Dashboard to open a shareable `?incident=<id>` destination while
preserving the current list filters in the URL. The detail screen provides:

- current Incident state, correlation identity, Failure count, and first/latest activity;
- chronological Failure Try metadata and validated `http`/`https` Airflow log links;
- every Diagnosis attempt with explicit `effective`, `REJECTED`, and `REUSED` provenance;
- classification, Root Cause, confidence, retry advice, review flag, sanitized Evidence, and
  recommended actions;
- expandable canonical Error Signature fields or an explicit unsignable state;
- immutable Incident state-transition history with initiator, actor, reason, selected-zone time, and
  original UTC time.

Rejected AI content is visually separated and labeled as non-effective. Reused content identifies
the direct original Diagnosis. API values are inserted as text rather than interpreted as HTML.

### Operator state changes

A Viewer sees the Incident state and audit trail as read-only. An Operator or Admin can acknowledge an
`OPEN` Incident, or resolve or ignore an `OPEN`, `ACKNOWLEDGED`, or `RECOVERED` Incident. Terminal
Incidents have no state-change buttons.

Every action opens a confirmation dialog with an optional reason. The request includes the state
currently displayed as `expected_status`; it never sends a browser-supplied audit actor. The API
derives that actor from the authenticated user's email. If another process changes the state
first, the UI handles the `409 Conflict` by refreshing the Incident and asking the Operator to
reconsider the new state.

## Error Signature exploration

The **Error Signatures** destination provides bounded, server-side exploration of recurring failure
groups. Search, classification, environment, DAG, Task, inclusive UTC date filters, stable sorting,
and pagination are represented in the URL.

Each Signature detail shows its canonical versioned fingerprint fields, exact Failure and Incident
counts, first/latest observations, latest validated non-reused Diagnosis context, and a zero-filled
daily occurrence trend. Its bounded occurrence table links every Failure Try back to the related
Incident while preserving applicable list filters. Incident details also link their canonical
Signature directly to this screen.

## Diagnosis History

The **Diagnosis History** destination provides bounded provenance records with source, validation,
classification, Error Signature, Failure Event, inclusive UTC date, confidence sorting, and stable
pagination filters represented in the URL. List rows distinguish the effective Diagnosis from
attempts that were superseded or have no effective result.

Diagnosis detail resolves reused content to its direct original while retaining the `REUSED` row's
own identity. It labels rejected AI output as non-effective, links to the effective fallback when
available, and shows sanitized Evidence, validation errors, recommended actions, extracted values,
version metadata, exact Failure Try context, related Incident, Error Signature, and validated
Airflow log URL. Provider response bodies and unsanitized logs are never requested.

## Timezone display

The header timezone selector offers `Asia/Seoul`, `UTC`, and the browser's local timezone. Its value
is retained in local browser storage, like the language preference. The database and API contracts
remain UTC: the browser converts each existing or new timestamp only for display. When a non-UTC
timezone is selected, list rows show the selected-zone value first and the original UTC value below
it; detail views show both values together. Selecting UTC suppresses the duplicate second value.

## Administration

An Admin-only destination lists local accounts and append-only Admin audit events. Admins can create
Viewer, Operator, or Admin accounts with a temporary password, change roles, enable or disable an
account, reset a temporary password, and revoke all sessions. Temporary passwords are write-only;
the API stores only Argon2id hashes and never returns the password or hash. Disabling a user and
resetting a password revoke existing sessions. A temporary-password session can only inspect its
own identity, change the password, or log out; the UI requires the change before opening any
operational destination. The API serializes Admin membership changes in PostgreSQL and refuses to
disable or demote the last active Admin.

The same destination manages encrypted outbound connections. Airflow, Ollama, and Slack connections
can be created or edited with Provider-specific allowlisted fields; other configured Providers remain
visible and can be disabled. Secret inputs are write-only, and configured values appear only as a
masked presence indicator. Airflow version, Ollama model-list, and Slack identity/channel checks are
explicitly read-only and store only a bounded status and error category. No connection test sends a
notification or performs an AI generation request.

## Browser security

UI responses set a same-origin Content Security Policy that blocks inline scripts, inline styles,
plugins, framing, and cross-origin API connections. They also set `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff`, and `X-Frame-Options: DENY`. The HTML entry point uses
`Cache-Control: no-store`.

Production sessions set the cookie `Secure` flag. Local plain-HTTP verification must use a
non-production `DAGSENTRY_ENVIRONMENT`, while production deployments must terminate HTTPS before
the API.

API values are inserted with DOM `textContent`; Diagnosis content and other server data are never
interpreted as HTML. Raw Task logs and Provider responses are not requested by the Dashboard.

## Current scope

The three v1.0 investigation destinations are implemented. The UI does not execute Airflow actions.
Automated accessibility and responsive contracts are covered by the test suite. Manual browser
and assistive-technology certification has not yet been completed.
