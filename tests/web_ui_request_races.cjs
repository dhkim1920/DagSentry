const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

// The shipped modules run unchanged. Only DOM and transport are replaced; the transport
// deliberately ignores AbortSignal to exercise guards even when cancellation arrives late.
class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.elements = []; this.value = ""; this.options = [{ value: "" }];
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set textContent(value) { this.children = [String(value)]; }
  get textContent() { return this.children.map(child => typeof child === "string" ? child : child.textContent).join(""); }
  prepend(...children) { this.children.unshift(...children); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
  querySelector(selector) {
    const name = selector?.match(/^\[name="(.+)"\]$/)?.[1];
    if (name) {
      const find = node => node.name === name ? node : node.children.map(child => typeof child === "object" ? find(child) : null).find(Boolean);
      return find(this) || null;
    }
    return new Element();
  }
  closest() { return new Element(); }
  reset() {} focus() {} close() { this.open = false; }
}
const web = path.resolve(__dirname, "../src/dagsentry/web");
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}
async function runtime() {
  const nodes = new Map();
  const context = vm.createContext({
    URLSearchParams, URL, AbortController, Headers, Error, Node: Element, Element,
    localStorage: { getItem: () => null }, navigator: { language: "en" },
    window: { location: { search: "", origin: "http://localhost" }, matchMedia: () => ({ matches: false }),
      TablerSparkline: class { static getInstance() { return null; } } },
    document: {
      cookie: "dagsentry_csrf=test%20token", body: new Element(), documentElement: new Element(),
      createElement: () => new Element(), querySelectorAll: () => [],
      querySelector: selector => {
        if (!nodes.has(selector)) nodes.set(selector, new Element());
        return nodes.get(selector);
      },
    },
    FormData: class { constructor(form) { this.form = form; } entries() { return this.form.elements.map(item => [item.name, item.value]); } },
    history: { replaceState: (_state, _title, url) => { context.url = url; } },
  });
  context.fetch = async () => response({ total: 0, items: [], date_from: "2026-09-01", date_to: "2026-09-07" });
  const cache = new Map();
  function load(file) {
    if (!cache.has(file)) cache.set(file, new vm.SourceTextModule(fs.readFileSync(file, "utf8"), { context, identifier: file }));
    return cache.get(file);
  }
  const entry = load(path.join(web, "app.js"));
  await entry.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
  const get = async name => {
    const module = cache.get(path.join(web, name));
    await module.evaluate();
    return module.namespace;
  };
  const { state } = await get("core/state.js");
  state.currentUser = { role: "VIEWER", display_name: "Viewer" };
  const { elements } = await get("core/elements.js");
  elements.connectionProvider.value = "AIRFLOW";
  return { context, get, state, elements };
}

const signature = marker => ({ id: marker, normalized_message: marker, last_seen_at: "2026-09-07T00:00:00Z", fingerprint: marker });
const incident = marker => ({ incident: { id: marker, status: "OPEN", dag_id: marker }, failures: [], transitions: [] });
const diagnosis = marker => ({ id: marker, root_cause: marker, evidence: [], recommended_actions: [], extracted_values: [], validation_errors: [], failure: {} });
const report = marker => ({ rule_based_report: { title: marker, highlights: [], priorities: [] }, statistics: { incidents: {}, error_signatures: {}, mean_time: {}, classification_counts: {} } });
const cases = [
  ["incidents/list", "loadIncidents", "/api/v1/incidents?", "range", "loading", n => ({ total: n, items: [] })],
  ["signatures", "loadSignatures", "/api/v1/error-signatures?", "signatureRange", "signatureLoading", n => ({ total: n, items: [] })],
  ["diagnoses", "loadDiagnoses", "/api/v1/diagnoses/history?", "diagnosisRange", "diagnosisLoading", n => ({ total: n, items: [] })],
  ["reports", "loadReports", "/api/v1/daily-reports?", "reportRange", "reportLoading", n => ({ total: n, items: [] })],
  ["incidents/detail", "loadIncidentDetail", "/api/v1/incidents/", "detailTitle", "detailLoading", incident],
  ["signatures", "loadSignatureDetail", "/api/v1/error-signatures/", "signatureDetailMessage", "signatureDetailLoading", signature],
  ["diagnoses", "loadDiagnosisDetail", "/api/v1/diagnoses/", "diagnosisDetailRootCause", "diagnosisDetailLoading", diagnosis],
  ["reports", "loadReportDetail", "/api/v1/daily-reports/", "reportDetailTitle", "reportDetailLoading", report],
];
for (const [file, name, prefix, marker, loading, payload] of cases) {
  test(`${name}: late parsed body cannot replace the latest result or URL`, async () => {
    const { context, get, elements } = await runtime();
    const view = await get(`views/${file}.js`);
    const original = context.fetch;
    const old = deferred(); let count = 0;
    context.fetch = async (url, options) => {
      if (url.startsWith(prefix) && !/\/(trend|occurrences|human-diagnoses)/.test(url) && !url.includes("limit=1&")) {
        if (++count === 1) return response(old.promise);
        return response(payload(22));
      }
      return original(url, options);
    };
    const first = view[name]("first");
    await flush(); // headers have arrived, body still pending
    assert.equal(await view[name]("second"), true, elements.detailError.textContent);
    const latest = elements[marker].textContent;
    assert.ok(latest.includes("22"), `${marker}: ${latest}`);
    const url = context.url;
    old.resolve(payload(11));
    assert.equal(await first, false);
    assert.equal(elements[marker].textContent, latest);
    assert.equal(context.url, url);
    assert.equal(elements[loading].hidden, true);
  });
}

test("an old rejected request cannot hide a newer loading indicator or show an error", async () => {
  const { context, get, elements } = await runtime();
  const view = await get("views/incidents/list.js");
  const old = deferred(), latest = deferred(); let count = 0;
  context.fetch = () => ++count === 1 ? old.promise : latest.promise;
  const first = view.loadIncidents(); const second = view.loadIncidents();
  old.reject(new Error("old transport failure"));
  assert.equal(await first, false);
  assert.equal(elements.loading.hidden, false);
  assert.equal(elements.error.hidden, true);
  latest.resolve(response({ items: [], total: 2 }));
  assert.equal(await second, true);
  assert.equal(elements.loading.hidden, true);
});

test("late summary and detail history stay with their owning view", async () => {
  const { context, get, elements } = await runtime();
  const list = await get("views/incidents/list.js");
  const detail = await get("views/incidents/detail.js");
  const summary = deferred(), history = deferred();
  const original = context.fetch;
  context.fetch = async (url, options) => {
    if (url.includes("limit=1&")) return response(summary.promise);
    if (url.includes("human-diagnoses")) return response(history.promise);
    if (url === "/api/v1/incidents/first") return response(incident("first"));
    return original(url, options);
  };
  const first = list.loadIncidents(); await flush();
  assert.equal(await detail.loadIncidentDetail("first"), true, elements.detailError.textContent); await flush();
  const { cancelViewRequests } = await get("core/requests.js");
  cancelViewRequests();
  elements.openTotal.textContent = "current";
  elements.humanDiagnosisHistory.textContent = "current";
  summary.resolve({ total: 99 }); history.resolve({ items: [{ revision: 99 }] });
  await first; await flush();
  assert.equal(elements.openTotal.textContent, "current");
  assert.equal(elements.humanDiagnosisHistory.textContent, "current");
});

test("trend changes keep only the latest range and abort on navigation", async () => {
  const { context, get, state, elements } = await runtime();
  const requests = await get("core/requests.js"); requests.beginViewRequest();
  const view = await get("views/signatures.js"); view.bindSignaturesEvents();
  state.currentSignatureId = "sig"; state.currentSignature = signature("sig");
  const old = deferred(), latest = deferred(); let count = 0;
  context.fetch = async () => response(++count === 1 ? old.promise : latest.promise);
  const first = elements.signatureTrend7.listeners.click();
  const second = elements.signatureTrend30.listeners.click();
  old.resolve({ date_from: "old", date_to: "old", items: [] }); await first;
  assert.equal(elements.signatureTrend30.disabled, true);
  latest.resolve({ date_from: "latest", date_to: "latest", items: [] }); await second;
  assert.equal(elements.signatureTrendRange.textContent, "latest – latest");
  assert.equal(state.currentSignatureTrendDays, 30);
  const pending = deferred(); context.fetch = async () => response(pending.promise);
  const third = elements.signatureTrend7.listeners.click(); requests.cancelViewRequests();
  pending.resolve({ date_from: "stale", date_to: "stale", items: [] }); await third;
  assert.equal(elements.signatureTrendRange.textContent, "latest – latest");
});

for (const status of [401, 503]) {
  test(`stale ${status} cannot clear a session; current ${status} has distinct handling`, async () => {
    const { context, get, state, elements } = await runtime();
    const view = await get("views/incidents/list.js");
    const body = deferred();
    context.fetch = async () => response(body.promise, status);
    const old = view.loadIncidents(); await flush();
    (await get("core/requests.js")).beginViewRequest();
    body.resolve({ detail: "old error" }); await old;
    assert.notEqual(state.currentUser, null);
    context.fetch = async () => response({ detail: "current error" }, status);
    assert.equal(await view.loadIncidents(), false);
    if (status === 401) {
      assert.equal(state.currentUser, null);
      assert.equal(elements.authError.textContent, "current error");
    } else {
      assert.notEqual(state.currentUser, null);
      assert.equal(elements.error.textContent, "current error");
    }
  });
}

test("logout invalidates a pending detail response before it can restore content", async () => {
  const { context, get, elements } = await runtime();
  const body = deferred(); context.fetch = async () => response(body.promise);
  const view = await get("views/incidents/detail.js");
  const pending = view.loadIncidentDetail("old"); await flush();
  (await get("core/session.js")).clearSession();
  elements.detailTitle.textContent = "signed out";
  body.resolve(incident("old")); assert.equal(await pending, false);
  assert.equal(elements.detailTitle.textContent, "signed out");
});

test("API helpers merge CSRF and caller headers, preserve signals and normalize failures", async () => {
  const { context, get } = await runtime();
  const api = await get("core/api.js");
  const controller = new AbortController(); let captured;
  context.fetch = async (_url, options) => { captured = options; return response({ saved: true }); };
  const payload = await api.apiJson("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal });
  assert.equal(payload.saved, true);
  assert.equal(captured.headers.get("X-CSRF-Token"), "test token");
  assert.equal(captured.headers.get("Content-Type"), "application/json");
  assert.equal(captured.signal, controller.signal);
  context.fetch = async () => response({ detail: [{ loc: ["body", "name"], msg: "Required" }] }, 422);
  await assert.rejects(api.apiJson("/api/test"), error => error.status === 422 && error.message === "name: Required");
  context.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error("bad JSON"); } });
  await assert.rejects(api.apiJson("/api/test"), /Unable to parse server response/);
  controller.abort();
  context.fetch = () => { throw new Error("must not send an already aborted request"); };
  await assert.rejects(api.apiJson("/api/test", { signal: controller.signal }), { name: "AbortError" });
});

const failedJob = {
  outbox_id: "job-id", failure_event_id: "event-id", environment: "test",
  dag_id: "failed-dag", task_id: "failed-task", dag_run_id: "run-id",
  try_number: 1, map_index: -1, attempt_count: 5,
  error_category: "attempts_exhausted", error_stage: "notification",
  error_type: "RetryableDiagnosisError", updated_at: "2026-10-19T00:00:00Z",
};

test("connection selection distinguishes saved, selected and disabled rows without claiming worker health", async () => {
  const { context, get, elements } = await runtime();
  context.window.location.search = "?view=admin&section=connections";
  const selections = [
    { purpose: "AIRFLOW", source: "database", provider: null, connection_id: null, version: null, secret_configured: false, fallback_providers: [] },
    { purpose: "LLM", source: "environment", provider: null, connection_id: null, version: null, secret_configured: false, fallback_providers: [] },
    { purpose: "NOTIFICATION", source: "environment", provider: "WEBHOOK", connection_id: null, version: null, secret_configured: false, fallback_providers: [] },
  ];
  const runtimeConfig = { scope: "api", environment: "test", encryption_key_configured: false, selections };
  const items = [true, false].map((enabled, index) => ({ id: `connection-${index}`, environment: "test", purpose: "NOTIFICATION", provider: "SLACK", display_name: "Saved Slack", version: 3, enabled, secret_configured: true }));
  context.fetch = async () => response({ total: 2, items, runtime_configuration: runtimeConfig });
  const view = await get("views/admin.js");
  await view.loadAdminDashboard();
  assert.match(elements.adminConnectionRows.textContent, /Stored only/);
  assert.match(elements.adminConnectionRows.textContent, /Disabled/);
  assert.match(elements.adminConnectionRows.textContent, /DAGSENTRY_NOTIFICATION_CONFIG_SOURCE=database/);
  assert.match(elements.connectionRuntimeSummary.textContent, /Enabled connection missing/);
  assert.match(elements.connectionRuntimeSummary.textContent, /Rule-based diagnosis only/);
  assert.equal(elements.connectionRuntimeKeyWarning.hidden, true);
  selections[2].source = "database";
  selections[2].secret_configured = true;
  await view.loadAdminDashboard();
  assert.match(elements.adminConnectionRows.textContent, /Selected by API settings/);
  assert.equal(elements.connectionRuntimeKeyWarning.hidden, false);
  context.fetch = async () => response({ total: 0, items: [] });
  await view.loadAdminDashboard();
  assert.match(elements.connectionRuntimeSummary.textContent, /Selection information unavailable/);
});
const jobList = items => ({ items, total: items.length, limit: 50, offset: 0 });
function findText(root, text) {
  if (root.textContent === text) return root;
  return root.children.filter(child => typeof child === "object").map(child => findText(child, text)).find(Boolean);
}

test("failed job requeue blocks double clicks, sends CSRF, and refreshes the list on success", async () => {
  const { context, get, state, elements } = await runtime();
  context.window.location.search = "?view=admin&section=jobs";
  state.currentUser = { role: "ADMIN", display_name: "Admin" };
  const pending = deferred(); let writes = 0; let captured; let listed = 0;
  context.fetch = async (_url, options) => {
    if (options.method === "POST") { writes += 1; captured = options; return response(pending.promise, 202); }
    return response(jobList(listed++ ? [] : [failedJob]));
  };
  const view = await get("views/admin.js");
  assert.equal(await view.loadAdminDashboard(), true);
  const button = findText(elements.workerJobRows, "Requeue job");
  assert.ok(button);
  const first = button.listeners.click(); await flush();
  assert.equal(button.disabled, true);
  await button.listeners.click();
  assert.equal(writes, 1);
  assert.equal(captured.headers.get("X-CSRF-Token"), "test token");
  pending.resolve({ outbox_id: "job-id", available_at: "2026-10-19T00:00:00Z" });
  await first;
  assert.equal(elements.workerJobRows.children.length, 0);
  assert.equal(elements.workerJobEmpty.hidden, false);
  assert.equal(elements.adminFeedback.hidden, false);
  assert.match(elements.adminFeedback.textContent, /queued for the Worker/);
});

test("failed job conflicts are shown; late requeue responses cannot navigate or restore feedback", async () => {
  const { context, get, elements } = await runtime();
  context.window.location.search = "?view=admin&section=jobs";
  context.fetch = async () => response(jobList([failedJob]));
  const view = await get("views/admin.js");
  await view.loadAdminDashboard();
  const button = findText(elements.workerJobRows, "Requeue job");
  context.fetch = async () => response({ detail: "Job already changed" }, 409);
  await button.listeners.click();
  assert.equal(elements.adminError.hidden, false);
  assert.equal(elements.adminError.textContent, "Job already changed");
  assert.equal(button.disabled, false);
  const pending = deferred();
  context.fetch = async () => response(pending.promise, 202);
  const oldWrite = button.listeners.click(); await flush();
  (await get("core/requests.js")).beginViewRequest();
  context.window.location.search = "?view=admin&section=connections";
  elements.adminFeedback.textContent = "new view";
  pending.resolve({ outbox_id: "job-id" }); await oldWrite;
  assert.equal(elements.adminFeedback.textContent, "new view");
  assert.equal(context.url, undefined);
});


test("connection forms cover every built-in provider and preserve omitted encrypted secrets", async () => {
  const { get, elements, state } = await runtime();
  const view = await get("components/connection-form.js");
  const names = ["AIRFLOW", "OLLAMA", "OPENAI", "AZURE_OPENAI", "ANTHROPIC", "BEDROCK", "SLACK", "WEBHOOK", "TEAMS", "DISCORD", "SMTP"];
  assert.deepEqual(Object.keys(view.MANAGED_CONNECTION_PROVIDERS).sort(), names.sort());
  for (const name of names) {
    elements.connectionProvider.value = name;
    elements.connectionEnvironment.value = "test";
    elements.connectionDisplayName.value = "Example";
    view.configureConnectionForm(true);
    const provider = view.MANAGED_CONNECTION_PROVIDERS[name];
    elements.connectionApiBaseUrl.value = provider.apiBaseUrl || "https://azure.example";
    elements.connectionModel.value = "model";
    elements.connectionChannel.value = "C123";
    elements.connectionSecret.value = provider.secretField ? "private-secret" : "";
    for (const field of provider.extra || []) {
      const input = elements.connectionExtraFields.querySelector(`[name="${field.key}"]`);
      if (field.secret || field.required) input.value = field.key === "recipients" ? "one@example.com, two@example.com" : "example";
      if (field.secret) assert.equal(input.type, "password");
    }
    const body = view.managedConnectionRequestBody();
    assert.equal(body.provider, name);
    assert.equal(body.purpose, provider.purpose);
    assert.ok(!JSON.stringify(body.non_secret_config).includes("private-secret"));
    if (name === "SMTP") {
      assert.equal(body.non_secret_config.port, 587);
      assert.equal(body.non_secret_config.use_starttls, true);
      assert.deepEqual(Array.from(body.non_secret_config.recipients), ["one@example.com", "two@example.com"]);
      assert.equal(body.secret.username, "example");
    }
    if (name === "BEDROCK") {
      assert.equal(body.non_secret_config.model_id, "model");
      assert.equal(body.secret.secret_access_key, "example");
    }
    if (name === "AZURE_OPENAI") {
      assert.equal(body.non_secret_config.endpoint, "https://azure.example");
      assert.ok(!("api_base_url" in body.non_secret_config));
    }
    if (["WEBHOOK", "TEAMS", "DISCORD", "SMTP", "BEDROCK"].includes(name)) {
      assert.ok(!("api_base_url" in body.non_secret_config));
      assert.equal(elements.connectionApiBaseUrl.required, false);
    }
    const connection = { ...body, id: "connection-id", version: 3, secret_configured: true };
    delete connection.secret;
    connection.non_secret_config.timeout_seconds = 17;
    view.editManagedConnection(connection);
    const updated = view.managedConnectionRequestBody();
    assert.equal(updated.expected_version, 3);
    assert.equal(updated.non_secret_config.timeout_seconds, 17);
    assert.ok(!("secret" in updated), "Blank edit must preserve ciphertext");
    for (const field of provider.extra || []) {
      if (field.secret) assert.equal(elements.connectionExtraFields.querySelector(`[name="${field.key}"]`).value, "");
    }
    state.editingConnection = null;
  }
});
