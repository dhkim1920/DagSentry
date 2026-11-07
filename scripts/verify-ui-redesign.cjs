const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const base = process.env.DAGSENTRY_UI_TEST_URL || "http://127.0.0.1:8011";
const phase = process.argv[2] || "after";
const output = path.resolve(process.env.DAGSENTRY_UI_SCREENSHOTS || ".audit/ui-redesign", phase);
assert.ok(["before", "after"].includes(phase));
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(new URL(base).hostname));

function contrast(foreground, background) {
  const luminance = color => color.match(/[\d.]+/g).slice(0, 3)
    .map(value => Number(value) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "ko-KR", timezoneId: "Asia/Seoul", reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto(`${base}/ui/`);
    await page.locator("#access-email").fill("admin@dagsentry.local");
    await page.locator("#access-password").fill("DagSentry-demo-2026!");
    await page.locator("#auth-submit").click();
    await page.locator("#results-panel").waitFor();
    const incidents = await (await context.request.get(`${base}/api/v1/incidents?status=OPEN&limit=20&offset=0`)).json();
    const incident = incidents.items[0];
    const diagnoses = await (await context.request.get(`${base}/api/v1/diagnoses?limit=20&offset=0`)).json();
    const detail = await (await context.request.get(`${base}/api/v1/incidents/${incident.id}`)).json();
    const routes = [
      ["incidents", "", "#results-panel"],
      ["incident-detail", `incident=${incident.id}`, "#detail-content"],
      ["signatures", "view=signatures", "#signature-results"],
      ["signature-detail", `view=signatures&signature=${incident.error_signature_id}`, "#signature-detail-content"],
      ["diagnoses", "view=diagnoses", "#diagnosis-results"],
      ["diagnosis-detail", `view=diagnoses&diagnosis=${diagnoses.items[0].id}`, "#diagnosis-detail-content"],
      ["reports", "view=reports", "#report-dashboard"],
      ["settings", "view=admin&section=users", "#admin-users-page"],
      ["report-settings", "view=admin&section=reports", "#report-schedule-form"],
    ];
    const measurements = [];
    const buttonContrast = [];
    for (const language of ["ko", "en"]) {
      await page.locator("#language-select").selectOption(language);
      for (const [name, query, selector] of routes) {
        await page.goto(`${base}/ui/?${query}`);
        await page.locator(selector).waitFor();
        await page.waitForLoadState("networkidle");
        assert.equal(await page.locator("html").getAttribute("lang"), language);
        if (phase === "after" && name === "incidents") {
          const button = page.locator('#filter-form button[type="submit"]');
          for (const hovered of [false, true]) {
            if (hovered) await button.hover();
            const colors = await button.evaluate(node => ({ color: getComputedStyle(node).color, background: getComputedStyle(node).backgroundColor }));
            const ratio = contrast(colors.color, colors.background);
            assert.ok(ratio >= 4.5, `Primary button contrast ${ratio} is below 4.5`);
            buttonContrast.push({ language, hovered, ratio });
          }
          await page.mouse.move(0, 0);
        }
        if (phase === "after" && name === "incident-detail") {
          const diagnosis = detail.failures.at(-1).diagnoses.find(item => item.effective && item.validation_status === "PASSED");
          const evidence = page.locator("#current-diagnosis-content > .diagnosis-investigation .evidence-list code");
          assert.deepEqual(await evidence.allTextContents(), diagnosis.evidence.map(item => item.text));
          assert.ok(await page.locator("#current-diagnosis-content > .diagnosis-investigation").isVisible());
        }
        if (phase === "after" && language === "en" && name === "report-settings") {
          assert.equal(await page.locator("#report-schedule-title").innerText(), "Report schedule settings");
          assert.equal(await page.locator("#report-schedule-save").innerText(), "Save settings");
          assert.ok((await page.locator("#report-schedule-preview").innerText()).startsWith("Runs daily at"));
        }
        for (const width of [1440, 768, 390, 320]) {
          await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
          await page.evaluate(() => window.scrollTo(0, 0));
          const contentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
          assert.ok(contentWidth <= width + 1, `${language}/${name}/${width} overflows (${contentWidth})`);
          if (phase === "after" && ["incidents", "incident-detail"].includes(name)) {
            const target = name === "incidents" ? "#incident-rows" : "#current-diagnosis-panel";
            const top = await page.locator(target).evaluate(node => node.getBoundingClientRect().top);
            assert.ok(top < (width === 1440 ? 1000 : 844), `${name}/${language}/${width}: primary content below viewport`);
          }
          await page.screenshot({ path: path.join(output, `${name}-${language}-${width}.png`), fullPage: true });
          measurements.push({ name, language, width, contentWidth });
        }
      }
    }
    if (phase === "after") {
      const baseline = JSON.parse(await fs.readFile(path.join(output, "../before/capture.json"), "utf8"));
      assert.deepEqual(incidents, baseline.incidentData, "Before/after must use the same incident data");
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(`${base}/ui/?status=OPEN&environment=${encodeURIComponent(incident.environment)}`);
      await page.locator("#results-panel").waitFor();
      const acknowledgedButton = page.locator('[data-incident-status="ACKNOWLEDGED"]');
      await acknowledgedButton.focus();
      await page.keyboard.press("Enter");
      await page.waitForURL(url => url.searchParams.get("status") === "ACKNOWLEDGED");
      await page.waitForLoadState("networkidle");
      assert.equal(await acknowledgedButton.evaluate(node => document.activeElement === node), true);
      assert.equal(await acknowledgedButton.getAttribute("aria-disabled"), "false");
      assert.equal(await acknowledgedButton.getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#status-filter").inputValue(), "ACKNOWLEDGED");
      assert.equal(new URL(page.url()).searchParams.get("environment"), incident.environment);
      await page.reload();
      await page.waitForLoadState("networkidle");
      assert.equal(new URL(page.url()).searchParams.get("status"), "ACKNOWLEDGED");
      assert.equal(await page.locator("#environment-filter").inputValue(), incident.environment);
      assert.equal(await acknowledgedButton.getAttribute("aria-pressed"), "true");
      await page.goto(`${base}/ui/?status=ACKNOWLEDGED&sort=failure_count&order=asc`);
      await page.locator("#results-panel").waitFor();
      await page.locator("#incident-rows a").first().click();
      await page.locator("#detail-content").waitFor();
      await page.goBack();
      await page.locator("#results-panel").waitFor();
      assert.equal(new URL(page.url()).searchParams.get("status"), "ACKNOWLEDGED");
      assert.equal(new URL(page.url()).searchParams.get("sort"), "failure_count");
      assert.equal(await acknowledgedButton.getAttribute("aria-pressed"), "true");
      await page.goForward();
      await page.locator("#detail-content").waitFor();
      assert.ok(new URL(page.url()).searchParams.get("incident"));
      await page.goBack();
      await page.locator("#results-panel").waitFor();
      await page.locator("#status-filter").selectOption("RESOLVED");
      await page.locator('#filter-form button[type="submit"]').click();
      await page.waitForURL(url => url.searchParams.get("status") === "RESOLVED");
      await page.waitForLoadState("networkidle");
      assert.equal(await page.locator('[data-incident-status][aria-pressed="true"]').count(), 0);
      const acknowledged = structuredClone(detail);
      acknowledged.incident.status = "ACKNOWLEDGED";
      let requested = false;
      await page.route(`**/api/v1/incidents/${incident.id}/status`, async route => {
        assert.equal(route.request().method(), "PATCH");
        assert.equal(route.request().postDataJSON().status, "ACKNOWLEDGED");
        requested = true;
        await route.fulfill({ json: { status: "ACKNOWLEDGED" } });
      });
      await page.goto(`${base}/ui/?incident=${incident.id}`);
      await page.locator("#detail-content").waitFor();
      await page.locator("#operator-actions").getByRole("button", { name: "Acknowledge", exact: true }).click();
      await page.locator("#transition-dialog").waitFor();
      await page.route(`**/api/v1/incidents/${incident.id}`, route => route.fulfill({ json: acknowledged }));
      await page.locator("#transition-confirm").click();
      await page.locator("#detail-feedback").waitFor();
      assert.equal(requested, true);
      assert.equal(await page.locator("#detail-status").getAttribute("data-status"), "ACKNOWLEDGED");
      assert.equal(await page.locator("#transition-dialog").isVisible(), false);
      await page.screenshot({ path: path.join(output, "transition-success-fixture.png"), fullPage: true });
    }
    await fs.writeFile(path.join(output, "capture.json"), JSON.stringify({ phase, incidentId: incident.id, incidentData: incidents, measurements, buttonContrast, fixtureOnly: phase === "after" ? ["successful status transition response"] : [] }, null, 2));
    console.log(JSON.stringify({ phase, output, screenshots: measurements.length, incidentId: incident.id }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
