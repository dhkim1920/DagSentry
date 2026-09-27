// Called by verify-ui-tabler.cjs against the disposable local demo. API responses
// are intercepted only for chart fixtures; no stored data is changed.
const assert = require("node:assert/strict");

module.exports = async function verifyTrend(page, signatureUrl, capture) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(signatureUrl);
  await page.locator("#signature-detail-content").waitFor({ state: "visible" });
  const points = page.locator("#signature-trend-points button");
  const bars = page.locator("#signature-trend-sparkline svg rect");
  assert.equal(await page.evaluate(() => window.TablerSparkline.NAME), "sparkline");
  assert.equal(await bars.count(), 7);
  const endpoint = "**/api/v1/error-signatures/*/trend?*";
  let mode = "normal";
  let payload;
  let release;
  await page.route(endpoint, async route => {
    if (mode === "error") return route.fulfill({ status: 500, json: { detail: "Trend fixture failure" } });
    if (mode === "loading") await new Promise(resolve => { release = resolve; });
    const query = new URL(route.request().url()).searchParams;
    const start = query.get("date_from");
    const end = query.get("date_to");
    const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
    payload = { date_from: start, date_to: end, bucket: "day", items: Array.from({ length: days }, (_, index) => ({
      date: new Date(Date.parse(start) + index * 86400000).toISOString().slice(0, 10),
      failure_count: mode === "zero" ? 0 : mode === "large" ? (index === 0 ? 10000 : 1) : index % 6 === 0 ? (index + 1) * 7 : 0,
    })) };
    await route.fulfill({ json: payload });
  });
  async function choose(days) {
    await Promise.all([
      page.waitForResponse(response => response.url().includes("/trend?")),
      page.locator(`#signature-trend-${days}`).click(),
    ]);
    await page.waitForFunction(() => document.querySelector("#signature-trend").getAttribute("aria-busy") === "false");
  }
  try {
    for (const days of [7, 30]) {
      await choose(days);
      assert.equal(await points.count(), days);
      assert.equal(await bars.count(), days);
      assert.equal(await page.locator(`#signature-trend-${days}`).getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#signature-trend-total").textContent(), String(payload.items.reduce((sum, item) => sum + item.failure_count, 0)));
      const heights = await bars.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute("height"))));
      const maximum = Number(await page.locator("#signature-trend-y-axis span").first().textContent());
      for (let i = 0; i < days; i++) {
        assert.ok(Math.abs(heights[i] - payload.items[i].failure_count / maximum * 180) < 0.00001);
        assert.ok((await points.nth(i).getAttribute("aria-label")).includes(payload.items[i].date));
      }
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        const geometry = await page.locator("#signature-trend").evaluate(el => ({ width: el.clientWidth, content: el.scrollWidth }));
        assert.ok(geometry.content <= geometry.width + 1, `Chart overflows at ${width}px`);
        const ticks = await page.locator("#signature-trend-dates time").evaluateAll(nodes => nodes
          .filter(node => node.textContent && getComputedStyle(node).visibility !== "hidden")
          .map(node => { const range = document.createRange(); range.selectNodeContents(node); const box = range.getBoundingClientRect(); return { left: box.left, right: box.right }; }));
        for (let i = 1; i < ticks.length; i++) assert.ok(ticks[i - 1].right <= ticks[i].left, `Overlapping dates at ${width}px`);
        await capture(page, `trend-${days}-${width}`);
      }
    }
    await points.last().focus();
    await page.keyboard.press("Home");
    assert.equal(await page.locator("#signature-trend-points :focus").getAttribute("aria-label"), await points.first().getAttribute("aria-label"));
    await page.keyboard.press("ArrowRight");
    assert.equal(await page.locator("#signature-trend-readout").textContent(), await points.nth(1).getAttribute("aria-label"));
    await page.keyboard.press("End");
    assert.equal(await page.locator("#signature-trend-points button[tabindex='0']").count(), 1);
    assert.notEqual(await points.last().evaluate(el => getComputedStyle(el).outlineStyle), "none");
    await points.nth(5).hover();
    assert.equal(await page.locator("#signature-trend-readout").textContent(), await points.nth(5).getAttribute("aria-label"));
    await points.nth(3).dispatchEvent("click");
    assert.equal(await page.locator("#signature-trend-readout").textContent(), await points.nth(3).getAttribute("aria-label"));

    mode = "zero";
    await choose(7);
    assert.equal(await page.locator("#signature-trend-empty").isVisible(), true);
    assert.equal(await page.locator("#signature-trend-total").textContent(), "0");
    assert.ok((await bars.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute("height"))))).every(height => height === 0));
    await capture(page, "trend-zero-320");

    mode = "large";
    await choose(30);
    const large = await bars.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute("height"))));
    assert.ok(Math.abs(large[0] / large[1] - 10000) < 0.001, "Small counts must not be rounded up to a tenth of the peak");
    await capture(page, "trend-large-320");

    mode = "loading";
    const pending = choose(7);
    await page.waitForFunction(() => document.querySelector("#signature-trend").getAttribute("aria-busy") === "true");
    assert.equal(await page.locator("#signature-trend-7").isDisabled(), true);
    assert.equal(await page.locator("#signature-trend-30").getAttribute("aria-pressed"), "true");
    await capture(page, "trend-loading-320");
    assert.ok(release);
    release(); await pending;

    mode = "error";
    const previousRange = await page.locator("#signature-trend-range").textContent();
    await choose(30);
    assert.equal(await page.locator("#signature-trend-error").isVisible(), true);
    assert.equal(await page.locator("#signature-trend-range").textContent(), previousRange);
    assert.equal(await page.locator("#signature-trend-7").getAttribute("aria-pressed"), "true");
    assert.equal(await bars.count(), 7);
    await capture(page, "trend-error-320");
    mode = "normal";
    await choose(30);
    assert.equal(await page.locator("#signature-trend-error").isVisible(), false);
    assert.equal(await bars.count(), 30);
  } finally {
    if (release) release();
    await page.unroute(endpoint);
  }
};
