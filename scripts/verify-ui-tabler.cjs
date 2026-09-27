// Optional browser checks against scripts/run-ui-demo.py. Playwright is a test-only
// dependency supplied through NODE_PATH; it is never included in the Python package.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const base = process.env.DAGSENTRY_UI_TEST_URL || "http://127.0.0.1:8000";
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(new URL(base).hostname), "Use a local demo server");
const output = process.env.DAGSENTRY_UI_SCREENSHOTS || "/tmp/dagsentry-tabler/browser";
const email = "admin@dagsentry.local";
const password = "DagSentry-demo-2026!";
const reportId = "00000000-0000-4000-8000-000000000001";
// A browser-only response fixture covers the report detail absent from the demo DB.
const report = {
  id: reportId, report_date: "2026-08-12", environment: "production",
  created_at: "2026-08-13T00:10:00Z", report_schema_version: 1, status: "DELIVERED", ai_summary_used: true,
  ai_summary: { key_changes: ["Repeated network failures"], priorities: ["Review unresolved incident"] },
  summary_provider: "test", provider: "webhook", attempt_count: 1,
  last_response_status: 200, last_error_category: null, delivered_at: "2026-08-13T00:10:00Z",
  statistics: {
    timezone: "UTC", failure_attempts: 3, affected_task_instances: 2, affected_dag_runs: 1,
    incidents: { new: 1, unresolved: 1, recovered: 0 },
    error_signatures: { new: 1, repeated: 2 }, classification_counts: { NETWORK: 3 },
    mean_time: { recovery_seconds: null, resolution_seconds: 120 }, top_failures: [],
  },
  rule_based_report: { title: "Daily report — production", overview: "Three failures require review.", highlights: ["Network failures: 3"], priorities: ["Investigate connectivity"] },
};

async function visible(page, selector) {
  await page.locator(selector).waitFor({ state: "visible" });
}

async function layout(page, name) {
  const modalOpen = await page.locator("dialog[open]").count() > 0;
  if (!modalOpen) {
    // Capture a consistent scroll/focus position after keyboard and form checks.
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    });
  }
  const dimensions = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
  assert.ok(dimensions.content <= dimensions.viewport + 1, `${name}: page overflow ${JSON.stringify(dimensions)}`);
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: !modalOpen });
}

async function login(page) {
  await page.goto(`${base}/ui/`);
  await page.locator("#access-email").fill(email);
  await page.locator("#access-password").fill(password);
  await page.locator("#auth-submit").click();
  await visible(page, "#results-panel");
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "ko-KR", timezoneId: "Asia/Seoul" });
    const page = await context.newPage();
    const errors = [];
    const violations = [];
    const external = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => {
      if (/Content Security Policy|Refused to (load|execute|apply)/i.test(message.text())) violations.push(message.text());
    });
    page.on("request", request => {
      if (!request.url().startsWith(base) && !request.url().startsWith("data:")) external.push(request.url());
    });
    await page.goto(`${base}/ui/`);
    await visible(page, "#auth-form");
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").getAttribute("href"), "#main-content");
    assert.notEqual(await page.locator(":focus").evaluate(el => getComputedStyle(el).outlineStyle), "none");
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await layout(page, `login-${width}`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await login(page);
    assert.equal(await page.locator("html").getAttribute("data-bs-theme"), "dark");
    const originalRows = await page.locator("#incident-rows").innerText();
    await fs.writeFile(path.join(output, "incident-data.txt"), originalRows);
    const listResponse = await context.request.get(`${base}/api/v1/incidents?status=OPEN&limit=20&offset=0`);
    const incidentPage = await listResponse.json();
    assert.ok(incidentPage.items.length);
    const incident = incidentPage.items[0];
    const incidentUrl = `${base}/ui/?incident=${incident.id}`;
    const signatureUrl = `${base}/ui/?view=signatures&signature=${incident.error_signature_id}`;
    const routes = [
      ["incidents", `${base}/ui/`, "#results-panel"],
      ["incident-detail", incidentUrl, "#detail-content"],
      ["signatures", `${base}/ui/?view=signatures`, "#signature-results"],
      ["signature-detail", signatureUrl, "#signature-detail-content"],
      ["diagnoses", `${base}/ui/?view=diagnoses`, "#diagnosis-results"],
      ["reports-empty", `${base}/ui/?view=reports`, "#report-dashboard"],
      ["settings", `${base}/ui/?view=admin`, "#admin-dashboard"],
    ];
    await page.goto(signatureUrl);
    await visible(page, "#signature-latest-diagnosis a");
    routes.push(["diagnosis-detail", `${base}${await page.locator("#signature-latest-diagnosis a").getAttribute("href")}`, "#diagnosis-detail-content"]);
    const positions = {};
    async function recordPosition(name, width, phase) {
      if (!["incidents", "incident-detail"].includes(name)) return;
      const selector = name === "incidents" ? "#incident-rows" : "#current-diagnosis-panel";
      const top = await page.locator(selector).evaluate(el => el.getBoundingClientRect().top);
      positions[`${phase}-${name}-${width}`] = Math.round(top);
      if (phase === "after") {
        assert.ok(top < (width === 1440 ? 1000 : 844), `${name}: primary content must start in the first viewport at ${width}px`);
        if (name === "incidents") {
          assert.equal(await page.locator("#results-panel").getByRole("columnheader").count(), 7);
          assert.equal(await page.locator("#incident-rows").getByRole("cell").count(), incidentPage.items.length * 7);
        }
      }
    }
    for (const [name, url, selector] of routes) {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(url);
      await visible(page, selector);
      await page.waitForLoadState("networkidle");
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
        await layout(page, `${name}-${width}`);
        await recordPosition(name, width, "after");
      }
    }
    // The same data can also be rendered with pre-migration assets for comparisons.
    if (process.env.DAGSENTRY_UI_BASELINE_ASSETS) {
      const baseline = process.env.DAGSENTRY_UI_BASELINE_ASSETS;
      await page.route("**/ui/**", async route => {
        const url = new URL(route.request().url());
        const file = url.pathname === "/ui/" ? "index.html" : path.basename(url.pathname);
        if (["index.html", "app.css", "app.js"].includes(file)) {
          await route.fulfill({ path: path.join(baseline, file), contentType: file.endsWith("html") ? "text/html" : file.endsWith("css") ? "text/css" : "text/javascript" });
        } else await route.continue();
      });
      for (const [name, url, selector] of routes) {
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(url); await visible(page, selector); await page.waitForLoadState("networkidle");
        for (const width of [1440, 390]) {
          await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
          await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: "instant" }));
          await page.screenshot({ path: path.join(output, `before-${name}-${width}.png`), fullPage: true });
          await recordPosition(name, width, "before");
        }
      }
      await page.unroute("**/ui/**");
    }
    await fs.writeFile(path.join(output, "content-positions.json"), JSON.stringify(positions, null, 2));
    // Details route, filters, ordering, browser back/forward and preferences survive reload.
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${base}/ui/?environment=${encodeURIComponent(incident.environment)}`);
    await visible(page, "#results-panel");
    assert.equal(await page.locator("#environment-filter").inputValue(), incident.environment);
    assert.equal(await page.locator("#filter-form details").evaluate(el => el.open), true);
    await page.locator("#clear-filters").click(); await visible(page, "#results-panel");
    assert.equal(await page.locator("#environment-filter").inputValue(), "");
    const filterSummary = page.locator("#filter-form details > summary");
    await filterSummary.focus(); await page.keyboard.press("Enter");
    assert.equal(await page.locator("#filter-form details").evaluate(el => el.open), true);
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#filter-form details").evaluate(el => el.open), false);
    await page.goto(`${base}/ui/?status=OPEN&task_id=${incident.task_id}&sort=failure_count&order=asc`);
    await visible(page, "#results-panel");
    assert.equal(await page.locator("#filter-form details").getAttribute("open"), "");
    await page.locator("#incident-rows a").first().click(); await visible(page, "#detail-content");
    assert.equal(new URL(page.url()).searchParams.get("task_id"), incident.task_id);
    await page.locator("#detail-back").click(); await visible(page, "#results-panel");
    assert.equal(await page.locator("#task-filter").inputValue(), incident.task_id);
    await page.goBack(); await visible(page, "#detail-content");
    await page.goForward(); await visible(page, "#results-panel");
    await page.locator("#language-select").selectOption("en");
    await page.locator("#timezone-select").selectOption("UTC");
    await page.reload(); await visible(page, "#results-panel");
    assert.equal(await page.locator("#language-select").inputValue(), "en");
    assert.equal(await page.locator("#timezone-select").inputValue(), "UTC");
    assert.equal(await page.locator("#dashboard-title").innerText(), "Incident response");
    // Native modal focus/Escape and request contracts. Write requests are intercepted.
    await page.goto(incidentUrl); await visible(page, "#detail-content");
    await page.locator("#operator-actions button").first().click(); await visible(page, "#transition-dialog");
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 }); await layout(page, `transition-${width}`);
    }
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Tab");
      // Native dialogs may move focus to browser chrome at the end of a cycle;
      // background page controls must remain inert in either case.
      assert.ok(await page.evaluate(() => document.activeElement === document.body
        || Boolean(document.activeElement.closest("#transition-dialog"))));
    }
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#transition-dialog").isVisible(), false);
    await page.locator("#operator-actions button").first().click();
    await page.locator("#transition-reason").fill("Browser verification only");
    let transitionRequest;
    await page.route("**/api/v1/incidents/*/status", async route => {
      transitionRequest = route.request();
      await route.fulfill({ status: 400, json: { detail: "Test rejection; no state changed" } });
    });
    await page.locator("#transition-confirm").click(); await visible(page, "#transition-error");
    assert.equal(transitionRequest.method(), "PATCH");
    assert.equal(transitionRequest.postDataJSON().expected_status, incident.status);
    const csrf = (await context.cookies()).find(cookie => cookie.name === "dagsentry_csrf");
    assert.ok(csrf);
    assert.equal(transitionRequest.headers()["x-csrf-token"], decodeURIComponent(csrf.value));
    assert.equal(transitionRequest.postDataJSON().actor, undefined);
    await page.locator("#transition-cancel").click();
    if (!await page.locator("#human-diagnosis-panel").evaluate(el => el.open)) {
      await page.locator("#human-diagnosis-panel > summary").click();
    }
    await page.locator("#human-diagnosis-actions button").first().click(); await visible(page, "#human-diagnosis-dialog");
    await layout(page, "operator-diagnosis-modal-320");
    await page.locator("#human-diagnosis-root-cause").fill("Long cause ".repeat(100));
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#human-diagnosis-dialog").isVisible(), false);
    await page.goto(`${base}/ui/?view=admin`); await visible(page, "#admin-user-rows");
    await page.locator("#admin-user-rows button").filter({ hasText: "Reset password" }).first().click();
    await visible(page, "#password-reset-dialog"); await layout(page, "password-reset-modal-320");
    assert.equal(await page.locator("#password-reset-value").getAttribute("type"), "password");
    await page.keyboard.press("Escape");
    for (const selector of ["#admin-dashboard > details > summary", "#admin-connections-title", "#admin-audit-title"]) {
      await page.locator(selector).click();
    }
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 }); await layout(page, `settings-expanded-${width}`);
    }
    await page.locator("#connection-secret-help-trigger").focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(".field-help-tooltip").evaluate(el => getComputedStyle(el).visibility), "visible");
    // Report settings (including real checkboxes) and a fixture report detail.
    await page.goto(`${base}/ui/?view=reports`); await visible(page, "#report-dashboard");
    await page.locator("#report-schedule-panel > summary").click(); await visible(page, "#report-schedule-form");
    const checked = await page.locator("#report-schedule-ai-summary").isChecked();
    await page.locator("#report-schedule-ai-summary").setChecked(!checked);
    assert.equal(await page.locator("#report-schedule-ai-summary").isChecked(), !checked);
    await layout(page, "report-settings-320");
    await page.route("**/api/v1/daily-reports**", async route => {
      await route.fulfill({ json: new URL(route.request().url()).pathname.endsWith(reportId) ? report : { items: [report], total: 1, limit: 20, offset: 0 } });
    });
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`${base}/ui/?view=reports`); await visible(page, "#report-results"); await layout(page, `reports-fixture-${width}`);
      await page.locator("#report-rows a").first().click(); await visible(page, "#report-detail-content"); await layout(page, `report-detail-fixture-${width}`);
    }
    // Exercise loading, empty, failure and hostile/long server text through real renderers.
    let mode = "loading";
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route("**/api/v1/incidents?*", async route => {
      if (mode === "loading") { await gate; return route.fulfill({ json: { ...incidentPage, items: [], total: 0 } }); }
      if (mode === "error") return route.fulfill({ status: 500, json: { detail: "Fixture request failure" } });
      const items = Array.from({ length: 20 }, (_, i) => ({ ...incident, id: `${incident.id}-${i}`, dag_id: "dag_" + "x".repeat(246), normalized_message: '<img src=x onerror="alert(1)">' + "Long message ".repeat(120) }));
      return route.fulfill({ json: { ...incidentPage, items, total: 41 } });
    });
    await page.goto(`${base}/ui/`); await visible(page, "#loading-state"); await layout(page, "loading-320");
    assert.equal(await page.locator("#filter-form").getAttribute("aria-busy"), "true");
    release(); await visible(page, "#empty-state"); await layout(page, "empty-320");
    mode = "error"; await page.locator("#refresh-incidents").click(); await visible(page, "#dashboard-error"); await layout(page, "request-error-320");
    mode = "long"; await page.locator("#refresh-incidents").click(); await visible(page, "#results-panel");
    assert.equal(await page.locator("#incident-rows img").count(), 0);
    assert.ok((await page.locator("#incident-rows").textContent()).includes("<img"));
    for (const width of [1440, 768, 390, 320]) { await page.setViewportSize({ width, height: 844 }); await layout(page, `long-text-${width}`); }
    await page.locator("#next-page").click();
    await page.waitForURL(url => url.searchParams.get("offset") === "20");
    await page.locator("#previous-page").click();
    await page.waitForURL(url => !url.searchParams.has("offset"));
    await page.unroute("**/api/v1/incidents?*");
    const detailResponse = await context.request.get(`${base}/api/v1/incidents/${incident.id}`);
    const longDetail = await detailResponse.json();
    const diagnosisFixture = structuredClone(longDetail);
    diagnosisFixture.current_human_diagnosis = {
      revision: 1, root_cause: "Operator-confirmed network cause", classification: "NETWORK",
      retry_decision: "NOT_RETRYABLE", recommended_actions: ["Restore the network route"],
      actor_identity: "Fixture operator", created_at: "2026-09-27T00:00:00Z", operator_notes: null,
    };
    await page.route(`**/api/v1/incidents/${incident.id}`, route => route.fulfill({ json: diagnosisFixture }));
    await page.goto(incidentUrl); await visible(page, "#detail-content");
    assert.equal(await page.locator("#incident-diagnoses > :first-child").getAttribute("id"), "human-diagnosis-panel");
    assert.equal(await page.locator("#human-diagnosis-panel").evaluate(el => el.open), true);
    assert.equal(await page.locator("#current-diagnosis-panel").evaluate(el => el.open), false);
    assert.ok((await page.locator("#human-diagnosis-content").innerText()).includes("Operator-confirmed network cause"));
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
      await layout(page, `operator-confirmed-${width}`);
    }
    diagnosisFixture.current_human_diagnosis = null;
    const latest = diagnosisFixture.failures.at(-1);
    assert.ok(latest.diagnoses.length, "Demo needs a diagnosis for the latest failure");
    const rejected = { ...latest.diagnoses[0], validation_status: "REJECTED", effective: false, root_cause: "Rejected conclusion must remain in history" };
    latest.diagnoses = [rejected];
    await page.reload(); await visible(page, "#detail-content");
    assert.equal(await page.locator("#incident-diagnoses > :first-child").getAttribute("id"), "current-diagnosis-panel");
    assert.equal(await page.locator("#human-diagnosis-panel").evaluate(el => el.open), false);
    assert.equal(await page.locator("#current-diagnosis-content .diagnosis-root-cause").count(), 0);
    await page.locator("#failure-history-panel > summary").click();
    assert.ok((await page.locator("#detail-failures").textContent()).includes(rejected.root_cause));
    await layout(page, "rejected-diagnosis-390");
    await page.unroute(`**/api/v1/incidents/${incident.id}`);
    longDetail.incident.dag_id = "long_dag_" + "x".repeat(240);
    longDetail.incident.task_id = "long_task_" + "y".repeat(239);
    for (const failure of longDetail.failures) {
      for (const diagnosis of failure.diagnoses) {
        diagnosis.root_cause = "Long evidence-backed explanation ".repeat(100);
        diagnosis.evidence = [{ line_id: 1, text: "unbroken_log_" + "z".repeat(1000) }];
      }
    }
    await page.route(`**/api/v1/incidents/${incident.id}`, route => route.fulfill({ json: longDetail }));
    await page.goto(incidentUrl); await visible(page, "#detail-content");
    await page.locator("#failure-history-panel > summary").click();
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 }); await layout(page, `long-detail-${width}`);
    }
    await page.unroute(`**/api/v1/incidents/${incident.id}`);
    await page.route("**/api/v1/auth/me", async route => {
      const response = await route.fetch(); const data = await response.json();
      data.must_change_password = true; await route.fulfill({ response, json: data });
    });
    await page.goto(`${base}/ui/`); await visible(page, "#change-password-form");
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 }); await layout(page, `temporary-password-${width}`);
    }
    await page.unroute("**/api/v1/auth/me");
    // Role-limited UI uses a response fixture; backend permission enforcement is
    // separately covered by the Python API tests.
    await page.route("**/api/v1/auth/me", async route => {
      const response = await route.fetch(); const data = await response.json();
      data.role = "VIEWER"; await route.fulfill({ response, json: data });
    });
    await page.goto(incidentUrl); await visible(page, "#detail-content");
    assert.equal(await page.locator("#admin-nav").isVisible(), false);
    assert.equal(await page.locator("#operator-controls").isVisible(), false);
    assert.equal(await page.locator("#human-diagnosis-actions").isVisible(), false);
    await visible(page, "#viewer-state-note"); await layout(page, "viewer-320");
    await page.locator("#disconnect-button").click(); await visible(page, "#auth-form");
    assert.deepEqual(errors, []);
    assert.deepEqual(violations, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({ result: "PASS", screenshots: output, checks: "views, responsive overflow, first-viewport content, diagnosis priority/rejection, labels, keyboard, dialogs, filters, URL/history, language/timezone, loading/empty/error, XSS text, pagination, CSRF request, viewer, logout", fixtureOnly: ["report content", "write failure", "operator/rejected diagnosis", "long text/pagination", "temporary password", "viewer role"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
