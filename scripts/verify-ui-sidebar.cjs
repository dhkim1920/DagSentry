const assert = require("node:assert/strict");

module.exports = async function verifySidebar(page, base, layout) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/ui/?view=admin&section=reports`);
  await page.locator("#report-schedule-form").waitFor({ state: "visible" });
  const sidebar = page.locator("#site-sidebar");
  const toggle = page.locator("#sidebar-toggle");
  const mainLeft = () => page.locator("main").evaluate(el => el.getBoundingClientRect().left);
  const expanded = await sidebar.boundingBox();
  assert.ok(await mainLeft() >= expanded.width, "Expanded sidebar must not cover content");
  await toggle.click();
  await page.mouse.move(1000, 500);
  assert.equal(await page.locator("html").getAttribute("data-bs-sidebar"), "folded-hover");
  assert.equal(await toggle.getAttribute("aria-expanded"), "false");
  const folded = await sidebar.boundingBox();
  assert.ok(folded.width < expanded.width / 2, "Folded rail should leave room for content");
  await page.waitForFunction(width => document.querySelector("main").getBoundingClientRect().left <= width + 1, folded.width);
  const contentLeft = await mainLeft();
  assert.ok(contentLeft >= folded.width);
  assert.equal(await page.locator("#incidents-nav .nav-link-icon").isVisible(), true);
  assert.equal(await page.locator("#incidents-nav .nav-link-title").evaluate(el => getComputedStyle(el).opacity), "0");
  await layout(page, "sidebar-folded-1440");
  await sidebar.hover();
  // Tabler animates width; wait for the actual expanded geometry.
  await page.waitForFunction(width => document.querySelector("#site-sidebar").getBoundingClientRect().width >= width - 1, expanded.width);
  assert.equal(await page.locator('[data-settings-link="reports"]').isVisible(), true);
  assert.equal(await mainLeft(), contentLeft, "Hover expansion must not reflow the page");
  await layout(page, "sidebar-hover-1440");
  await page.mouse.move(1000, 500);
  await toggle.focus(); await page.keyboard.press("Tab");
  await page.locator("#incidents-nav").focus();
  assert.notEqual(await page.locator("#incidents-nav").evaluate(el => getComputedStyle(el).outlineStyle), "none");
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#incidents-nav .nav-link-title")).opacity === "1");
  await page.locator("#signatures-nav").click();
  await page.locator("#signature-dashboard").waitFor({ state: "visible" });
  assert.equal(await page.locator("#signatures-nav").getAttribute("aria-current"), "page");
  await toggle.click();
  assert.equal(await page.locator("html").getAttribute("data-bs-sidebar"), null);

  for (const width of [1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${base}/ui/?view=admin&section=reports`);
    await page.locator("#report-schedule-form").waitFor({ state: "visible" });
    if (width >= 992) {
      assert.ok(await mainLeft() >= (await sidebar.boundingBox()).width);
    } else {
      assert.equal(await sidebar.isVisible(), false);
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      await toggle.focus(); await page.keyboard.press("Enter");
      assert.equal(await sidebar.isVisible(), true);
      await page.locator("#admin-nav").focus(); await page.keyboard.press("Escape");
      assert.equal(await sidebar.isVisible(), false);
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true);
      await toggle.click();
    }
    await layout(page, `sidebar-expanded-${width}`);
    if (width < 992) {
      await page.locator('[data-settings-link="connections"]').click();
      await page.locator("#admin-connections-page").waitFor({ state: "visible" });
      assert.equal(await sidebar.isVisible(), false, "Choosing a mobile item closes navigation");
      assert.equal(await toggle.evaluate(el => el === document.activeElement), true);
      await page.goBack(); await page.locator("#admin-reports-page").waitFor({ state: "visible" });
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  assert.equal(await sidebar.isVisible(), true, "Mobile collapse must not hide desktop navigation");
};
