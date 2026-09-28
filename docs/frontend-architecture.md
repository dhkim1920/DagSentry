# Frontend architecture

The API serves `src/dagsentry/web/` directly at `/ui/`. The Python wheel includes the entire
asset tree. `index.html` loads `app.js` as a native ES module. There is no bundler, package.json,
Node runtime dependency, or change to the existing Tabler components and DOM IDs.

| Location | Responsibility |
| --- | --- |
| `app.js` | Bind feature events once, apply preferences, restore filters and the authenticated session |
| `core/api.js`, `core/session.js`, `core/auth.js` | CSRF headers and API errors; session access; login, logout and password change |
| `core/requests.js` | Own and cancel the active view and trend request lifetimes |
| `core/router.js` | Restore filters, build allowlisted queries, preserve list/detail URL state |
| `core/navigation.js`, `core/shell.js`, `core/sidebar.js` | Dispatch views, browser history, visible sections and navigation |
| `core/state.js`, `core/elements.js` | Explicit shared mutable state and DOM handles |
| `core/dom.js`, `core/i18n.js`, `i18n/ko.js` | Safe DOM construction, timestamp formatting, translation and Korean strings |
| `views/` | Signature, Diagnosis, Report and Admin rendering, loading and event binding |
| `views/incidents/` | Incident list, detail, transitions and human diagnosis, each with its own event binding |
| `components/` | Shared badges, diagnosis cards and managed connection forms |
| `styles/` | Layout, component and view rules in the existing cascade order |

English strings remain the source text in HTML and rendering functions; there is no duplicate
English dictionary. Korean fixed strings live in `i18n/ko.js`; dynamic translation patterns and
the mutation observer live in `core/i18n.js`.

Views import shared helpers explicitly. They do not import the entry point. Authentication accepts
the view-loading callback during startup so it does not depend on view modules. Shared presentation
helpers belong in `core/dom.js` or `components/`, not in another view. Admin loads Report schedules
through the Reports module because report scheduling appears inside Settings.

State is a plain object, not a reactive store. URL restoration, event handlers and asynchronous
loaders share it explicitly. Keep feature-only functions private and export only functions consumed
by another module. Bind new feature listeners in that feature's `bind…Events` function and call it
once from `app.js`. Keep text rendering through `textContent` and DOM APIs.

`app.css` imports `layout.css`, `components.css`, and `views.css` in that order. The initial split
preserves the original rule sequence exactly, including responsive overrides in `views.css`.
Do not reorder the files or rules without checking the cascade. Nested JS and CSS assets receive
the same CSP and `Cache-Control: no-store` handling as the entry files.

## Requests and errors

Views use `apiRequest` for responses without a required body and `apiJson` for JSON payloads.
Both merge CSRF and caller headers, accept an `AbortSignal`, and throw `ApiError` with the HTTP
status and a normalized detail message. Only `core/api.js` calls `fetch`. Authentication handles
`401` by clearing the session and showing login; `503` is displayed as a service error in the
current view and does not invalidate a session.

Each list or detail load starts `beginViewRequest()`. This cancels the previous view and trend.
Summary counts, similar diagnoses, operator history and report schedules share their owning
request. `beginTrendRequest()` cancels an earlier trend without discarding its parent detail.
Every asynchronous render/error/loading cleanup checks `request.isCurrent()`. The API helpers also
check cancellation after receiving headers and after parsing JSON, so delayed error bodies cannot
sign out a newer session. Navigation, logout and password-change screens cancel view requests.

Incident transitions and human diagnosis refresh detail through callbacks installed by
`bindIncidentDetailEvents`, keeping the import graph acyclic. Mutations capture the Incident ID
and form values before asynchronous reads. Canceling a browser request does not roll back a server
write, so a canceled mutation is never retried automatically.

## Verification

```sh
.venv/bin/pytest tests/test_web_ui.py tests/test_web_ui_behavior.py tests/test_web_ui_tabler.py -q
```

The asset contract tests follow imports through the API, checking that nested assets are served.
Node is optional for tests only: the behavior runner skips when Node is unavailable. Its module
tests link the actual `app.js` graph with `vm.SourceTextModule` and exercise the URL helpers through
native exports. Request-race tests run the actual view modules against a transport that ignores
cancellation, checking late JSON bodies, summaries/history, errors, loading state and logout. Existing focused rendering tests still extract individual functions.

The `frontend-browser` job in `.github/workflows/ci.yml` runs these tests with Node 22, then
Playwright 1.58.2 and Chromium on every pull request and push to `main`. It waits for an isolated
SQLite demo server to become ready, fails on browser assertions, stops the server on exit, and
uploads screenshots/logs for seven days even on failure. Node and Playwright are test-only.
Repository branch-protection settings determine whether merging requires this job; adding the
workflow does not change those settings. Local passes do not prove the remote job has run.

For browser checks, start `scripts/run-ui-demo.py` against its disposable SQLite database (use `--port` for a separate local test server) and run
`scripts/verify-ui-tabler.cjs` with the test-only Playwright installation described in
[Tabler verification](ui-tabler-migration.md#re-run-browser-checks). The runner covers authentication,
views, filters/history, dialogs, roles, language/timezone, request errors, CSRF, XSS text and
320/390/768/1440px layouts. Some API responses are browser fixtures; this is not an external
Provider or production deployment test.

The README images are the runner's `incidents-1440.png` and `diagnosis-detail-1440.png`, copied to
`docs/images/incidents.png` and `docs/images/diagnosis-evidence.png`. They show seeded data, not
real operational data. Regenerate them after visible UI changes and inspect them before committing.

The 2026-09-28 module refactor passed the 36 focused pytest checks (including native module tests)
and the Chromium browser runner. These results describe this change's local verification scope.
Historical T00–T16 implementation notes are retained in [Development history](development-history.ko.md).

The request-lifetime follow-up passed 645 local tests excluding PostgreSQL integration, including
15 native request-race/API tests, plus the Chromium runner, Ruff, mypy and actionlint. The local
Python environment includes the Airflow extra. The new browser CI job has not run remotely yet.
For comparison, the [8cf9fa2 CI run](https://github.com/dhkim1920/DagSentry/actions/runs/36426040128)
failed in the existing mypy, Airflow 3.1.8 and production Compose jobs; the Airflow 3.3.1 job passed.
Those results must not be replaced with a claim that local UI checks certify the entire CI pipeline.
