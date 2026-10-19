const assert = require("node:assert/strict");

// Local browser fixtures cover degradation; PostgreSQL/process tests cover real liveness.
module.exports = async (page, base, capture) => {
  const language = await page.locator("#language-select").inputValue();
  const routePattern = "**/api/v1/admin/operations";
  const snapshot = {
    observed_at: "2026-10-19T00:00:00Z", version: "0.3.0.dev0", api_status: "ONLINE", database_status: "READY",
    worker: { status: "ONLINE", online_instances: 2, last_heartbeat_at: "2026-10-19T00:00:00Z", last_success_at: "2026-10-18T23:58:00Z" },
    scheduler: { status: "OFFLINE", last_heartbeat_at: "2026-10-18T23:00:00Z", last_success_at: null },
    queue: { pending: 7, ready: 2, delayed: 5, processing: 1, stale_processing: 1, dead: 3, completed: 25, oldest_ready_at: "2026-10-18T23:59:00Z", oldest_ready_age_seconds: 60 },
  };
  let fail = false;
  await page.route(routePattern, route => route.fulfill(fail
    ? { status: 503, json: { detail: "Operational status is unavailable" } }
    : { json: snapshot }));
  try {
    await page.goto(`${base}/ui/?view=admin&section=operations`);
    await page.locator("#language-select").selectOption("ko");
    await page.waitForFunction(() => document.querySelector('[data-metric="pending"] dd')?.textContent === "7");
    assert.equal(await page.locator('[data-settings-link="operations"]').getAttribute("aria-current"), "page");
    assert.equal(await page.locator('[data-service="worker"] [data-status]').getAttribute("data-status"), "ONLINE");
    assert.equal(await page.locator('[data-service="scheduler"] [data-status]').getAttribute("data-status"), "OFFLINE");
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await capture(page, `operations-${width}`);
    }
    await page.route("**/health/ready", route => route.fulfill({ status: 503, json: { detail: "Database is not ready" } }));
    fail = true;
    await page.locator("#operations-refresh").click();
    await page.locator("#operations-error").waitFor({ state: "visible" });
    assert.equal(await page.locator('[data-service="database"] [data-status]').getAttribute("data-status"), "UNAVAILABLE");
    assert.equal(await page.locator('[data-metric="pending"] dd').textContent(), "—");
    assert.equal(await page.locator('[data-service="worker"] [data-status]').getAttribute("data-status"), "UNKNOWN");
    await capture(page, "operations-database-unavailable-320");
    await page.unroute("**/health/ready");
    fail = false;
    snapshot.worker.status = "UNKNOWN";
    snapshot.worker.online_instances = 0;
    snapshot.worker.last_heartbeat_at = null;
    await page.locator("#operations-refresh").click();
    await page.waitForFunction(() => document.querySelector('[data-metric="pending"] dd')?.textContent === "7"
      && document.querySelector('[data-service="worker"]')?.textContent.includes("관측 정보 없음"));
    assert.ok((await page.locator('[data-service="worker"]').textContent()).includes("관측 정보 없음"));
    await page.locator("#language-select").selectOption("en");
    await page.waitForFunction(() => document.querySelector("#admin-dashboard-title").textContent === "Operational overview");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await capture(page, "operations-unobserved-en-1440");
    await page.locator('#admin-operations-page a[href="/ui/?view=admin&section=jobs"]').click();
    await page.locator("#admin-jobs-page").waitFor({ state: "visible" });
  } finally {
    await page.unroute(routePattern);
    await page.unroute("**/health/ready");
    await page.locator("#language-select").selectOption(language);
  }
};
