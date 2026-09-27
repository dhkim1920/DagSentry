# Tabler UI migration

The existing HTML/JavaScript frontend now uses the official, unmodified Tabler
Core 1.6.0 stylesheet. All assets are served through the existing `/ui` Python
package mount. Dark mode is fixed with `data-bs-theme="dark"`; no theme switch,
frontend server, Node production dependency or CDN was added.

## Implementation

- `src/dagsentry/web/index.html`: Tabler navbar/nav, page headers, cards, tables,
  buttons, alerts, badges, form controls/switches and modal structure across login,
  incidents, error signatures, diagnoses, daily reports and administration.
- `src/dagsentry/web/app.js`: dynamic elements use the same component classes.
  Existing status/source/validation text and datasets remain; colors are mapped
  to Tabler utilities. Event targets and API/authentication/CSRF logic remain.
- `src/dagsentry/web/app.css`: removes the former component skin and custom
  type scale. Retains application grids, evidence/history/trend layouts,
  responsive rules and keyboard focus. Native `<dialog>` continues to provide
  modal behavior; Tabler JavaScript/Bootstrap/Popper are not loaded.
- `src/dagsentry/web/vendor/tabler-1.6.0/`: pinned official CSS and full Tabler,
  Bootstrap and Normalize MIT notices. See `THIRD_PARTY_NOTICES.md` for exact
  sources, hashes, scope and license review.
- `pyproject.toml`: includes third-party notices in the wheel. The existing source
  distribution also includes notices, CSS and license files.

No API, database, authentication, authorization or CSP changes were made. An
inventory comparison against the pre-migration shell preserved all 331 IDs and
non-class attributes, and all 127 form controls/options and their attributes.
Decorative close glyphs were replaced by Tabler's CSS close icon; their accessible
labels and event IDs remain.

On narrow screens navigation remains in document flow. Incident rows stack their
existing fields vertically; other wide tables scroll within a focusable region.
A positioned table wrapper prevents
absolutely positioned screen-reader labels from creating page-wide overflow.
Long identifiers wrap, while error previews retain the existing two-line limit.
The full incident/signature details remain available.

## Verification

The local demo database was reused without reset. Before/after incident-list text
matched exactly. Screenshots of the original and migrated assets use the same
local demo data at 1440×1000 and 390×844. The browser runner also checks 768px and
320px widths. Screenshot artifacts are local, under
`/private/tmp/dagsentry-tabler/browser/` (not packaged or committed).

- Python suite: 645 passed, 24 skipped. Skips require external PostgreSQL/Ollama
  services, which were not configured.
- Ruff: passed. Mypy (`src tests`): passed, 174 source files.
- Node syntax and behavior tests: passed; includes status/source/validation badge
  semantics and removal of stale status colors.
- Packaging: built sdist and wheel, inspected each for the pinned CSS, all three
  full licenses and third-party notices; no Node/Playwright runtime included.
- Browser: login/logout, lists/details, persisted language/timezone, filters and
  sort in URLs, detail/back/browser history, pagination, native modal keyboard
  handling, CSRF/expected-status request fields, loading/empty/failure displays,
  long text and safe rendering of HTML-like server data, and viewer restrictions.
  No unexpected page errors, CSP violations or external page requests.
- Rejected state changes, populated reports, long text/pagination and restricted
  role/password-change responses use browser fixtures. No real incident state,
  password, report schedule or external connection is changed by this runner.
  Backend authorization and write behavior remain covered by the Python suite.

The screenshot review uses Chromium. Firefox/WebKit, real mobile devices and a
full screen-reader/accessibility audit have not been run. Screenshots are review
artifacts, not a committed pixel-regression baseline.

## Incident information hierarchy follow-up

The list now has inline counts instead of three large summary cards. Status and
DAG are the primary filters; environment, task and sorting remain in additional
filters, which open automatically when a URL contains an active value. Count
scope is available in an adjacent disclosure. All seven list fields remain;
narrow screens show each incident vertically with labels instead of requiring a
horizontal scan.

The detail starts with the operator-confirmed diagnosis when present, otherwise
the latest failure's effective, validated automatic diagnosis. An empty operator
diagnosis is collapsed but its publication controls remain available. State
changes follow the diagnosis and recommendations; the confirmation dialog and
permission rules are unchanged. Failure counts and dates move from the header
to the supporting column. Source and automated validation badges are visible
beside the automatic conclusion. Rejected attempts, reuse provenance, evidence
and audit history remain available through the existing detail/history controls.

Same-data Chromium comparisons (Korean, Asia/Seoul, navigation expanded in both
versions) measured these positions from the top of the viewport, rounded to px:

| Content | Viewport | Before | After |
| --- | --- | ---: | ---: |
| First incident row | 1440 × 1000 | 625 | 465 |
| First incident row | 390 × 844 | 1220 | 674 |
| Current automatic diagnosis panel | 1440 × 1000 | 631 | 279 |
| Current automatic diagnosis panel | 390 × 844 | 1022 | 453 |

Artifacts: `/private/tmp/dagsentry-ui-hierarchy/browser/`, including paired
screenshots and `content-positions.json`. These measurements concern the seeded
demo's default filters and short identifiers; expanded filters and long content
naturally require more scrolling. The first mobile row begins in the viewport;
its full content can extend below it.

The full Python suite again passed (645 passed, 24 external-service skips), as
did Ruff, Mypy and Node syntax checks. Browser checks include 1440/768/390/320px,
first-viewport content, operator diagnosis priority, rejected conclusions kept in
history, URL-restored environment filters, keyboard disclosures, table semantics,
modals, permissions, loading/empty/error states and long untrusted text. Operator
and rejected diagnosis scenarios use response fixtures, without database writes.
Only Chromium was exercised; the browser/accessibility limitations above remain.

## Re-run browser checks

Start the demo with `uv run python scripts/run-ui-demo.py` (do not use `--reset`
unless you intend to replace demo data). Install Playwright 1.58.2 in a separate
test directory, then its Chromium headless shell. For example:

```sh
npm install --prefix /tmp/dagsentry-ui-check --no-save --ignore-scripts playwright@1.58.2
PLAYWRIGHT_BROWSERS_PATH=/tmp/dagsentry-ui-check/browsers /tmp/dagsentry-ui-check/node_modules/.bin/playwright install chromium --only-shell
NODE_PATH=/tmp/dagsentry-ui-check/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/dagsentry-ui-check/browsers node scripts/verify-ui-tabler.cjs
```

The runner accepts only a localhost server and uses the disposable demo account.
`DAGSENTRY_UI_SCREENSHOTS` can select an output directory. Optionally set
`DAGSENTRY_UI_BASELINE_ASSETS` to a directory containing the prior `index.html`,
`app.css`, and `app.js`; the runner will also render comparison screenshots using
those assets without replacing repository files.
