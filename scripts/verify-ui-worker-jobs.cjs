// Browser-only queue fixtures exercise UI transitions; Python tests enforce real
// permissions, transactions, and job state. No Worker or external provider runs.
const assert = require("node:assert/strict");

module.exports = async (page, base, layout) => {
  const language = await page.locator("#language-select").inputValue();
  let jobs = Array.from({ length: 56 }, (_, index) => ({
    outbox_id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    failure_event_id: `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    environment: index < 3 ? "test" : "bulk",
    dag_id: index === 0 ? '<img src=x onerror="window.jobXss=true">' : "failed_dag",
    task_id: "diagnose_failed_task", dag_run_id: "scheduled__2026-10-19T00:00:00Z",
    map_index: -1, try_number: 2, attempt_count: 5,
    error_category: "attempts_exhausted", error_stage: "notification",
    error_type: "RetryableDiagnosisError", updated_at: "2026-10-19T00:00:00Z",
  }));
  const listRoute = "**/api/v1/admin/worker-jobs?*";
  const writeRoute = "**/api/v1/admin/worker-jobs/*/requeue";
  let failWrite = true;
  let releaseWrite;
  let writes = 0;
  let csrf;
  await page.route(listRoute, route => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    const selected = jobs.filter(job => !params.get("environment") || job.environment === params.get("environment"));
    return route.fulfill({ json: { items: selected.slice(offset, offset + limit), total: selected.length, offset, limit } });
  });
  await page.route(writeRoute, async route => {
    writes += 1;
    csrf = route.request().headers()["x-csrf-token"];
    if (failWrite) {
      await route.fulfill({ status: 409, json: { detail: "This job is no longer failed. Refresh the list before retrying." } });
      return;
    }
    await new Promise(resolve => { releaseWrite = resolve; });
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    jobs = jobs.filter(job => job.outbox_id !== id);
    await route.fulfill({ status: 202, json: { outbox_id: id, failure_event_id: "event-id", available_at: "2026-10-19T01:00:00Z" } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${base}/ui/?view=admin&section=jobs`);
    await page.locator("#language-select").selectOption("ko");
    await page.waitForFunction(() => document.querySelector("#admin-dashboard-title").textContent === "실패 작업 재처리");
    await page.waitForFunction(() => document.querySelector("#worker-job-rows").children.length === 50);
    assert.equal(await page.locator('[data-settings-link="jobs"]').getAttribute("aria-current"), "page");
    await page.locator("#worker-job-next").click();
    await page.waitForFunction(() => document.querySelector("#worker-job-rows").children.length === 6);
    assert.equal(new URL(page.url()).searchParams.get("offset"), "50");
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#worker-job-rows").children.length === 6);
    assert.equal(await page.locator("#worker-job-next").isEnabled(), false);
    await page.locator("#worker-job-previous").click();
    await page.waitForFunction(() => document.querySelector("#worker-job-rows").children.length === 50);
    await page.locator("#worker-job-environment").fill("absent");
    await page.locator("#worker-job-filter button").click();
    await page.locator("#worker-job-empty").waitFor({ state: "visible" });
    assert.equal(await page.locator("#worker-job-table").isVisible(), false);
    await layout(page, "worker-jobs-empty-1440");
    await page.locator("#worker-job-environment").fill("test");
    await page.locator("#worker-job-filter button").click();
    await page.waitForFunction(() => document.querySelector("#worker-job-rows").children.length === 3);
    assert.equal(await page.locator("#worker-job-rows img").count(), 0);
    assert.ok((await page.locator("#worker-job-rows").textContent()).includes("<img src=x"));
    await page.locator("#worker-job-rows summary").first().focus();
    await page.keyboard.press("Enter");
    const button = page.locator("#worker-job-rows button").first();
    await button.waitFor({ state: "visible" });
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      const box = await button.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width, "Requeue must be reachable without horizontal scrolling");
      assert.equal(await page.locator("#worker-job-table").getByRole("table").count(), 1);
      assert.equal(await page.locator("#worker-job-table").getByRole("columnheader").count(), 5);
      await layout(page, `worker-jobs-review-${width}`);
    }
    await button.click();
    await page.locator("#admin-error").waitFor({ state: "visible" });
    assert.ok((await page.locator("#admin-error").textContent()).includes("이미 작업 상태가 변경"));
    assert.equal(await button.isEnabled(), true);
    assert.ok(csrf, "Requeue must carry the session CSRF token");
    failWrite = false;
    await button.click();
    assert.equal(await button.isEnabled(), false);
    assert.equal(writes, 2);
    assert.ok(releaseWrite);
    releaseWrite();
    await page.locator("#admin-feedback").waitFor({ state: "visible" });
    assert.equal(await page.locator("#worker-job-rows tr").count(), 2);
    assert.equal(await page.locator("#admin-error").isVisible(), false);
    assert.ok((await page.locator("#admin-feedback").textContent()).includes("Worker 대기열에 등록"));
    await layout(page, "worker-jobs-requeued-320");
    await page.locator("#language-select").selectOption("en");
    await page.waitForFunction(() => document.querySelector("#admin-dashboard-title").textContent === "Failed diagnosis jobs");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await layout(page, "worker-jobs-en-1440");
  } finally {
    releaseWrite?.();
    await page.unroute(listRoute);
    await page.unroute(writeRoute);
    await page.locator("#language-select").selectOption(language);
  }
};
