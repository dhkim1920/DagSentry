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
  querySelector() { return new Element(); }
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
