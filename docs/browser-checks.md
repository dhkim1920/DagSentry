# Browser regression checks

The optional `scripts/verify-ui-tabler.cjs` runner accepts `DAGSENTRY_UI_BROWSER=chromium`,
`firefox`, or `webkit` (default Chromium). Use the test-only Playwright version pinned in
`.github/workflows/ci.yml`, install the corresponding browser, and start
`scripts/run-ui-demo.py` against a disposable database. Set `DAGSENTRY_UI_TEST_URL` to its local
address and `DAGSENTRY_UI_SCREENSHOTS` to a separate output directory for each engine.

```shell
NODE_PATH=/path/to/test/node_modules \
PLAYWRIGHT_BROWSERS_PATH=/path/to/test/browsers \
DAGSENTRY_UI_BROWSER=firefox \
DAGSENTRY_UI_TEST_URL=http://127.0.0.1:8000 \
DAGSENTRY_UI_SCREENSHOTS=/tmp/dagsentry-browser/firefox \
node scripts/verify-ui-tabler.cjs
```

The CI browser job has independent Chromium, Firefox and WebKit matrix entries. Each preserves
screenshots, demo logs, and `report.json` containing the engine/version, operating system,
observation time, checked views and remaining manual checks. A failure preserves `failure.png`
when a page is available. These are browser regression artifacts, not a complete accessibility audit.

All main routes are checked at 1440, 1024, 768, 390 and 320 CSS pixels. The five priority views
(Incident list, Signature list/detail, Diagnosis list/detail) also run at 720 CSS pixels with
reduced motion and forced-colors emulation when supported. Checks cover skip-link focus transfer,
the next Tab remaining in the main content, focus outlines, labeled keyboard-scrollable regions,
stopped loading animations and page overflow. Unsupported forced-colors emulation is recorded as
`forcedColorsEmulated: false`; it must not be reported as verified. Screenshots need visual review
for clipping, readability and color distinctions. Some states, including notification content,
write failures and Viewer roles, use local response fixtures; browser tests send no notifications.

A 720px viewport checks reflow at half a 1440px desktop width. It does not exercise actual browser
200% zoom, OS high-contrast mode, screen readers, or Safari. Playwright WebKit is an engine test,
not evidence that the installed Safari release passed. Verify those separately before declaring
the browser/accessibility TODO complete. Include VoiceOver/Safari and NVDA/Firefox or Chromium,
keyboard-only navigation, modal focus and Escape, horizontal table scrolling, Korean/English
switching, status/provenance text, and focus visibility at actual 200% browser zoom.

## Local results, 2026-10-19 UTC

| Engine | Platform | Result |
| --- | --- | --- |
| Chromium 145.0.7632.6 | macOS | Full runner passed, including five accessibility views and forced-colors emulation |
| Firefox 146.0.1 | macOS | Full runner passed, including five accessibility views and forced-colors emulation |
| WebKit 26.0 / Playwright build 2248 | macOS | Launch did not finish, including an elevated retry; no pass recorded |

The Firefox run exposed inconsistent native skip-link focus behavior. The link now explicitly
focuses the main landmark, which has `tabindex=-1`; the following Tab reaches a main-content control.
The matrix configuration adds a Linux WebKit execution path. Its CI result, actual Safari,
actual 200% zoom, OS high contrast and screen-reader checks remain unverified by this local run.
