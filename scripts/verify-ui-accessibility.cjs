const assert = require("node:assert/strict");

module.exports = async (page, routes, capture) => {
  const priority = new Set(["incidents", "signatures", "signature-detail", "diagnoses", "diagnosis-detail"]);
  const checked = [];
  await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "active" });
  const forcedColors = await page.evaluate(() => matchMedia("(forced-colors: active)").matches);
  try {
    for (const [name, url, selector] of routes.filter(([name]) => priority.has(name))) {
      // A 720 CSS-pixel viewport tests reflow equivalent to half a 1440px desktop.
      // This does not automate the browser's actual zoom control or assistive technology.
      await page.setViewportSize({ width: 720, height: 500 });
      await page.goto(url);
      await page.locator(selector).waitFor({ state: "visible" });
      await page.waitForLoadState("networkidle");
      assert.equal(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches), true);
      // Inspect hidden loading indicators too: the page has already finished loading.
      const animations = await page.locator(".spinner-border").evaluateAll(nodes => nodes
        .map(node => getComputedStyle(node).animationDuration));
      assert.ok(animations.length > 0, `${name}: loading indicators must be present`);
      assert.ok(animations.every(duration => parseFloat(duration) <= 0.01), `${name}: reduced motion must stop loading animation`);
      await page.keyboard.press("Tab");
      const skip = page.locator(":focus");
      assert.equal(await skip.getAttribute("href"), "#main-content", `${name}: keyboard must reach skip link first`);
      assert.notEqual(await skip.evaluate(node => getComputedStyle(node).outlineStyle), "none");
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => document.activeElement.id), "main-content");
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.querySelector("main").contains(document.activeElement)), true);
      for (const region of await page.locator('[role="region"][tabindex="0"]:visible').all()) {
        const overflows = await region.evaluate(node => node.scrollWidth > node.clientWidth + 1);
        if (!overflows) continue;
        assert.ok(await region.getAttribute("aria-label") || await region.getAttribute("aria-labelledby"));
        await region.focus();
        await region.evaluate(node => { node.scrollLeft = 0; });
        await page.keyboard.press("ArrowRight");
        await page.waitForFunction(node => node.scrollLeft > 0, await region.elementHandle());
      }
      await capture(page, `accessibility-${name}-720`);
      checked.push(name);
    }
    assert.equal(checked.length, 5);
    return { views: checked, reducedMotion: true, forcedColorsEmulated: forcedColors,
      reflowWidth: 720, actualBrowserZoom: "manual check required", screenReader: "manual check required" };
  } finally {
    await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "none" });
  }
};
