// Local demo only. Every connection write is intercepted; no credentials are sent externally.
const assert = require("node:assert/strict");

module.exports = async function verifyConnections(page, base, capture) {
  await page.goto(`${base}/ui/?view=admin&section=connections`);
  await page.locator("#connection-form").waitFor({ state: "visible" });
  const language = await page.locator("#language-select").inputValue();
  await page.locator("#language-select").selectOption("en");
  const writes = [];
  const pattern = "**/api/v1/admin/connections/*";
  const listPattern = "**/api/v1/admin/connections?*";
  await page.route(pattern, async route => {
    assert.equal(route.request().method(), "PUT");
    writes.push(route.request());
    await route.fulfill({ status: 422, json: { detail: "Connection browser fixture" } });
  });
  try {
    const names = await page.locator("#connection-provider option").evaluateAll(nodes => nodes.map(node => node.value));
    assert.equal(names.length, 11);
    for (const provider of names) {
      await page.locator("#connection-provider").selectOption(provider);
      await page.locator("#connection-environment").fill("browser-test");
      await page.locator("#connection-display-name").fill("Browser fixture");
      for (const [id, value] of [["connection-api-base-url", "https://fixture.invalid/api"], ["connection-model", "fixture-model"], ["connection-channel", "C123"]]) {
        if (await page.locator(`#${id}`).isVisible()) await page.locator(`#${id}`).fill(value);
      }
      if (await page.locator("#connection-secret").isVisible()) {
        await page.locator("#connection-secret").fill(["WEBHOOK", "TEAMS", "DISCORD"].includes(provider) ? "https://fixture.invalid/hook?key=fake-secret" : "fake-secret");
      }
      for (const input of await page.locator("#connection-extra-fields input").all()) {
        const name = await input.getAttribute("name");
        if (await input.getAttribute("type") === "checkbox") continue;
        await input.fill({ region: "ap-northeast-2", host: "smtp.fixture.invalid", port: "587", sender: "from@example.com", recipients: "one@example.com, two@example.com" }[name] || "fake-secret");
      }
      const expected = writes.length + 1;
      await page.locator("#connection-save").click();
      await page.locator("#admin-error").waitFor({ state: "visible" });
      assert.equal(writes.length, expected, `${provider}: form must submit`);
      const request = writes.at(-1);
      const body = request.postDataJSON();
      assert.equal(body.provider, provider);
      assert.ok(request.headers()["x-csrf-token"]);
      assert.ok(!JSON.stringify(body.non_secret_config).includes("fake-secret"));
      if (provider === "SMTP") {
        assert.deepEqual(body.non_secret_config.recipients, ["one@example.com", "two@example.com"]);
        assert.equal(body.non_secret_config.use_starttls, true);
        assert.equal(body.secret.password, "fake-secret");
        assert.equal(body.secret.username, "fake-secret");
      }
      if (provider === "BEDROCK") assert.equal(body.secret.secret_access_key, "fake-secret");
      if (provider === "AZURE_OPENAI") assert.equal(body.non_secret_config.endpoint, "https://fixture.invalid/api");
    }
    const fixture = {
      ...writes.at(-1).postDataJSON(), id: "00000000-0000-4000-8000-000000000010",
      version: 3, secret_configured: true, last_test_status: null,
    };
    delete fixture.secret;
    fixture.non_secret_config.timeout_seconds = 17;
    const runtime = {
      scope: "api", environment: "browser-test", encryption_key_configured: false,
      selections: [
        { purpose: "AIRFLOW", source: "database", provider: null, connection_id: null, version: null, secret_configured: false, fallback_providers: [] },
        { purpose: "LLM", source: "environment", provider: null, connection_id: null, version: null, secret_configured: false, fallback_providers: [] },
        { purpose: "NOTIFICATION", source: "environment", provider: "WEBHOOK", connection_id: null, version: null, secret_configured: false, fallback_providers: ["SMTP"] },
      ],
    };
    await page.route(listPattern, route => route.fulfill({ json: { total: 1, items: [fixture], limit: 20, offset: 0, runtime_configuration: runtime } }));
    await page.reload();
    await page.locator("#admin-connection-rows button").first().waitFor({ state: "visible" });
    assert.ok((await page.locator("#admin-connection-rows").textContent()).includes("Stored only"));
    assert.ok((await page.locator("#connection-runtime-summary").textContent()).includes("Enabled connection missing"));
    assert.equal(await page.locator("#connection-runtime-key-warning").isVisible(), false);
    runtime.selections[2] = { purpose: "NOTIFICATION", source: "database", provider: "SMTP", connection_id: fixture.id, version: 3, secret_configured: true, fallback_providers: [] };
    await page.reload();
    await page.locator("#connection-runtime-key-warning").waitFor({ state: "visible" });
    assert.ok((await page.locator("#admin-connection-rows").textContent()).includes("Selected by API settings"));
    assert.equal(await page.locator("#admin-connection-rows").getByRole("button", { name: "Test", exact: true }).count(), 0);
    await page.locator("#admin-connection-rows").getByRole("button", { name: "Edit", exact: true }).click();
    assert.equal(await page.locator("#connection-secret").inputValue(), "");
    assert.equal(await page.locator("#connection-extra-username").inputValue(), "");
    await page.locator("#language-select").selectOption("ko");
    assert.equal(await page.locator('label[for="connection-extra-host"]').innerText(), "SMTP 호스트");
    assert.equal(await page.locator("#connection-runtime-title").innerText(), "연결 설정 선택 상태 (API 기준)");
    assert.ok((await page.locator("#admin-connection-rows").textContent()).includes("API 설정 기준 선택됨"));
    await page.locator("#language-select").selectOption("en");
    await page.locator("#connection-save").click();
    await page.locator("#admin-error").waitFor({ state: "visible" });
    const update = writes.at(-1).postDataJSON();
    assert.equal(update.expected_version, 3);
    assert.equal(update.non_secret_config.timeout_seconds, 17);
    assert.ok(!("secret" in update), "Blank edit must preserve stored encrypted credentials");
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await capture(page, `connections-smtp-${width}`);
    }
    await page.locator("#connection-runtime-title").scrollIntoViewIfNeeded();
    await page.locator('summary').filter({ hasText: "How to apply saved connections" }).click();
    await capture(page, "connection-apply-help-320");
  } finally {
    await page.unroute(pattern);
    await page.unroute(listPattern);
    await page.locator("#language-select").selectOption(language);
  }
};
