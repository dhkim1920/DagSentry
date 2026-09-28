const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const web = path.join(__dirname, "../src/dagsentry/web");
const source = fs.readdirSync(web, { recursive: true })
  .filter(name => name.endsWith(".js") && !name.startsWith("vendor/"))
  .map(name => fs.readFileSync(path.join(web, name), "utf8")).join("\n");

// Execute the shipped functions without bootstrapping a browser session or network requests.
function runtime(names, globals = {}) {
  let sequence = 0;
  const context = vm.createContext({
    URLSearchParams,
    beginViewRequest: () => {
      const current = ++sequence;
      return { isCurrent: () => current === sequence, signal: undefined };
    },
    handleApiError: () => false,
    ...globals,
  });
  context.apiJson = async (...args) => (await (globals.fetch || globals.adminApiRequest)(...args)).json();
  context.state = context;
  for (const name of [...new Set(["offsetFromUrl", "restoreFilters", "filterQuery", "replaceFilterUrl", ...names])]) {
    const match = source.match(new RegExp(`(?:async )?function ${name}\\([^]*?^}`, "m"));
    assert.ok(match, `Missing function ${name}`);
    vm.runInContext(match[0], context);
  }
  return context;
}

const diagnosisRuntime = runtime(["latestFailureDiagnosis"]);
const valid = { id: "current", effective: true, validation_status: "PASSED" };

test("a new undiagnosed failure never inherits an older conclusion", () => {
  assert.equal(diagnosisRuntime.latestFailureDiagnosis([
    { diagnoses: [valid] }, { diagnoses: [] },
  ]), null);
});

test("rejected and non-effective attempts cannot become the current conclusion", () => {
  assert.equal(diagnosisRuntime.latestFailureDiagnosis([{ diagnoses: [
    { ...valid, effective: false },
    { ...valid, validation_status: "REJECTED" },
  ] }]), null);
  assert.equal(diagnosisRuntime.latestFailureDiagnosis([]), null);
});

test("the latest failure's effective validated result is selected, including reused content", () => {
  const reused = { ...valid, source: "REUSED" };
  assert.equal(diagnosisRuntime.latestFailureDiagnosis([
    { diagnoses: [valid] },
    { diagnoses: [{ ...valid, effective: false }, reused] },
  ]), reused);
});

function control(name, defaultValue = "", select = false) {
  return {
    name, value: defaultValue, defaultValue,
    tagName: select ? "SELECT" : "INPUT",
    options: [{ value: defaultValue }],
  };
}

function filters(signature = false) {
  const advanced = [
    control("environment"),
    control("task_id"),
    control("sort", signature ? "last_seen_at" : "last_failure_at", true),
    control("order", "desc", true),
    ...(signature ? [control("classification"), control("date_from"), control("date_to")] : []),
  ];
  const details = { open: false, querySelectorAll: () => advanced };
  const form = {
    elements: [control("status", "OPEN", true), control("dag_id"), ...advanced],
    querySelector: () => details,
  };
  const context = runtime(["revealAdvancedFilters", "setFiltersFromUrl", "setSignatureFiltersFromUrl"], {
    window: { location: { search: "" } },
    elements: { filterForm: form, signatureFilterForm: form },
  });
  return { context, form, details, restore: signature ? "setSignatureFiltersFromUrl" : "setFiltersFromUrl" };
}

test("URL task filters and non-default ordering remain visible and preserve pagination", () => {
  const { context, form, details, restore } = filters();
  context.window.location.search = "?task_id=load&sort=failure_count&order=asc&offset=20";
  context[restore]();
  assert.equal(details.open, true);
  assert.equal(form.elements.find((item) => item.name === "task_id").value, "load");
  assert.equal(form.elements.find((item) => item.name === "order").value, "asc");
  assert.equal(context.currentOffset, 20);
  context.window.location.search = "?environment=production";
  context[restore]();
  assert.equal(details.open, true);
  assert.equal(form.elements.find((item) => item.name === "environment").value, "production");
  assert.equal(form.elements.find((item) => item.name === "task_id").value, "");
  assert.equal(context.currentOffset, 0);
});

test("signature date and classification filters expand even when loaded directly from a link", () => {
  for (const query of ["environment=production", "date_from=2026-09-01", "date_to=2026-09-27", "classification=NETWORK", "order=asc"]) {
    const { context, details, restore } = filters(true);
    context.window.location.search = `?view=signatures&${query}`;
    context[restore]();
    assert.equal(details.open, true, query);
  }
});

test("default sorting leaves advanced filters collapsed", () => {
  const { context, details, restore } = filters();
  context.window.location.search = "?sort=last_failure_at&order=desc";
  context[restore]();
  assert.equal(details.open, false);
});

test("diagnosis date filters restored from a URL are expanded without changing their values", () => {
  const dates = [control("date_from"), control("date_to")];
  const details = { open: false, querySelectorAll: () => dates };
  const form = { elements: [control("source_type"), control("error_signature_id"), ...dates], querySelector: () => details };
  const context = runtime(["revealAdvancedFilters", "setDiagnosisFiltersFromUrl"], {
    window: { location: { search: "?view=diagnoses&source_type=RULE&date_from=2026-09-01&date_to=2026-09-27&offset=20" } },
    elements: { diagnosisFilterForm: form },
  });
  context.setDiagnosisFiltersFromUrl();
  assert.equal(details.open, true);
  assert.deepEqual(dates.map(item => item.value), ["2026-09-01", "2026-09-27"]);
  assert.equal(form.elements[0].value, "RULE");
  assert.equal(context.currentDiagnosisOffset, 20);
  context.window.location.search = "?view=diagnoses&source_type=AI";
  context.setDiagnosisFiltersFromUrl();
  assert.equal(details.open, false);
  assert.ok(dates.every(item => item.value === ""));
});

test("status totals use the submitted environment, DAG and task, independently of pagination", async () => {
  const requests = [];
  const elements = { openTotal: {}, acknowledgedTotal: {} };
  const context = runtime(["loadIncidentSummary"], {
    elements,
    authHeaders: () => ({}),
    fetch: async (url) => {
      const params = new URL(url, "http://localhost").searchParams;
      requests.push(params);
      return { ok: true, json: async () => ({ total: params.get("status") === "OPEN" ? 4 : 2 }) };
    },
  });
  const submitted = new URLSearchParams({
    environment: "production", dag_id: "orders & payments", task_id: "load",
    status: "RESOLVED", sort: "failure_count", order: "asc", limit: "20", offset: "40",
  });
  const original = submitted.toString();
  await context.loadIncidentSummary(submitted, context.beginViewRequest());
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.get("environment"), "production");
    assert.equal(request.get("dag_id"), "orders & payments");
    assert.equal(request.get("task_id"), "load");
    assert.equal(request.get("limit"), "1");
    assert.equal(request.get("offset"), "0");
  }
  assert.deepEqual(requests.map((request) => request.get("status")), ["OPEN", "ACKNOWLEDGED"]);
  assert.equal(submitted.toString(), original);
  assert.equal(elements.openTotal.textContent, "4");
  assert.equal(elements.acknowledgedTotal.textContent, "2");

  requests.length = 0;
  await context.loadIncidentSummary(new URLSearchParams(), context.beginViewRequest());
  assert.ok(requests.every((request) => !request.has("environment") && !request.has("dag_id") && !request.has("task_id")));
});

test("loading incidents passes the submitted query to the summary even if the form changes", async () => {
  const submitted = new URLSearchParams("environment=production&dag_id=orders");
  let formQuery = submitted;
  let summaryQuery;
  const context = runtime(["loadIncidents"], {
    elements: { error: {} },
    setLoading: () => {},
    queryFromFilters: () => formQuery,
    updateUrl: () => {},
    authHeaders: () => ({}),
    fetch: async () => {
      formQuery = new URLSearchParams("environment=staging");
      return { ok: true, json: async () => ({ items: [], total: 0 }) };
    },
    renderPage: () => {},
    loadIncidentSummary: async (params) => { summaryQuery = params; },
  });
  assert.equal(await context.loadIncidents(), true);
  assert.equal(summaryQuery, submitted);
});

// A minimal DOM stand-in for text and link rendering; this does not test browser layout.
class TestNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.classList = {
      add: (...values) => { this.className = [...new Set([...(this.className || "").split(" ").filter(Boolean), ...values])].join(" "); },
      remove: (...values) => { this.className = (this.className || "").split(" ").filter(value => !values.includes(value)).join(" "); },
    };
  }
  set textContent(value) { this.children = [String(value)]; }
  get textContent() { return this.children.map((child) => typeof child === "string" ? child : child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

test("incident rows show only the error description and preserve filtered detail navigation", () => {
  const elements = { rows: new TestNode("tbody") };
  const context = runtime(["textElement", "appendCell", "applyStatusColor", "renderRows"], {
    Node: TestNode,
    document: { createElement: (tag) => new TestNode(tag) },
    elements,
    window: { location: { search: "?environment=production&offset=20" } },
    timestampBlock: (value) => value,
  });
  const incident = {
    id: "incident-123", status: "OPEN", environment: "production",
    dag_id: "orders", task_id: "load", error_signature_id: "signature-123",
    failure_count: 2, first_failure_at: "first-timestamp", last_failure_at: "last-timestamp",
    exception_class: "ValueError", normalized_message: "<script>failure</script>",
  };
  context.renderRows([incident]);
  const row = elements.rows.children[0];
  assert.equal(row.children.length, 7);
  const summary = row.children[1].children[0];
  assert.deepEqual(summary.children.map((child) => child.textContent), ["ValueError", "<script>failure</script>"]);
  assert.ok(summary.children.every((child) => child.tag !== "a"));
  assert.ok(!row.textContent.includes(incident.id));
  assert.ok(!row.textContent.includes(incident.first_failure_at));
  assert.ok(row.textContent.includes(incident.last_failure_at));
  const link = row.children[6].children[0];
  const params = new URL(link.href, "http://localhost").searchParams;
  assert.equal(params.get("incident"), incident.id);
  assert.equal(params.get("environment"), "production");
  assert.equal(params.get("offset"), "20");

  for (const [exception, message, expected] of [
    [null, null, "Error summary unavailable"],
    [null, "message only", "message only"],
    ["ValueError", "ValueError", "ValueError"],
  ]) {
    context.renderRows([{ ...incident, exception_class: exception, normalized_message: message }]);
    const error = elements.rows.children[0].children[1].children[0];
    assert.equal(error.children.length, 1);
    assert.equal(error.textContent, expected);
  }
});

test("Tabler badges retain source, validation and status meanings when reused", () => {
  const context = runtime(["textElement", "applyStatusColor", "sourceBadge", "validationBadge"], {
    document: { createElement: tag => new TestNode(tag) },
  });
  const badge = new TestNode("span");
  badge.className = "status-badge badge";
  context.applyStatusColor(badge, "OPEN");
  assert.ok(badge.className.includes("bg-red-lt"));
  context.applyStatusColor(badge, "RESOLVED");
  assert.ok(badge.className.includes("bg-green-lt"));
  assert.ok(!badge.className.includes("bg-red-lt"));
  context.applyStatusColor(badge, "IGNORED");
  assert.ok(badge.className.includes("bg-secondary-lt"));
  for (const source of ["AI", "RULE", "REUSED", "OPERATOR"]) {
    const result = context.sourceBadge(source);
    assert.equal(result.textContent, source);
    assert.equal(result.dataset.source, source);
    assert.ok(result.className.includes("badge"));
  }
  for (const status of ["PASSED", "REJECTED", "CONFIRMED", "WITHDRAWN"]) {
    const result = context.validationBadge(status);
    assert.equal(result.textContent, status);
    assert.equal(result.dataset.validation, status);
    assert.ok(result.className.includes(["REJECTED", "WITHDRAWN"].includes(status) ? "bg-red-lt" : "bg-green-lt"));
  }
});

function adminRuntime(request) {
  const rendered = [];
  const context = runtime(["adminPageFromUrl", "loadAdminDashboard"], {
    window: { location: { search: "?view=admin" } },
    elements: {
      adminLoading: { hidden: true }, adminContent: { hidden: true },
      adminFeedback: { hidden: false }, adminError: { hidden: true },
      adminUserTotal: {}, adminConnectionTotal: {},
    },
    showAdminPage() {},
    adminApiRequest: async (...args) => (await request(...args)).json(),
    renderAdminUsers: items => rendered.push(["users", items]),
    renderAdminConnections: items => rendered.push(["connections", items]),
    renderAdminAudit: items => rendered.push(["audit", items]),
    showAdminError: message => rendered.push(["error", message]),
  });
  return { context, rendered };
}

test("settings links load only their own data; old and unknown links open users", async () => {
  const requests = [];
  const { context, rendered } = adminRuntime(async url => {
    requests.push(url);
    return { json: async () => ({ total: 1, items: [url] }) };
  });
  for (const [section, endpoint] of [
    ["", "users?limit=200"], ["unknown", "users?limit=200"],
    ["connections", "connections?limit=200"], ["audit", "audit-events?limit=100"],
  ]) {
    context.window.location.search = `?view=admin&section=${section}`;
    assert.equal(await context.loadAdminDashboard(), true);
    assert.equal(requests.at(-1), `/api/v1/admin/${endpoint}`);
    assert.equal(context.elements.adminContent.hidden, false);
    assert.equal(context.elements.adminLoading.hidden, true);
  }
  assert.equal(requests.length, 4);
  assert.deepEqual(rendered.map(entry => entry[0]), ["users", "users", "connections", "audit"]);
});

test("a delayed settings response cannot overwrite a more recent page", async () => {
  let finishUsers;
  const { context, rendered } = adminRuntime(url => url.includes("/users?")
    ? new Promise(resolve => { finishUsers = resolve; })
    : Promise.resolve({ json: async () => ({ items: ["connection"] }) }));
  const users = context.loadAdminDashboard();
  context.window.location.search = "?view=admin&section=connections";
  await context.loadAdminDashboard();
  finishUsers({ json: async () => ({ items: ["user"] }) });
  assert.equal(await users, false);
  assert.deepEqual(rendered, [["connections", ["connection"]]]);
});

test("settings request failures display an error and end loading", async () => {
  const { context, rendered } = adminRuntime(async () => { throw new Error("Unavailable"); });
  assert.equal(await context.loadAdminDashboard(), false);
  assert.equal(context.elements.adminLoading.hidden, true);
  assert.equal(context.elements.adminContent.hidden, true);
  assert.equal(rendered[0][0], "error");
});

test("report settings load their own data and keep load errors visible", async () => {
  const { context } = adminRuntime(() => { throw new Error("Unrelated admin request"); });
  context.window.location.search = "?view=admin&section=reports";
  for (const loaded of [true, false]) {
    context.loadReportSchedules = async () => loaded;
    assert.equal(context.adminPageFromUrl(), "reports");
    assert.equal(await context.loadAdminDashboard(), loaded);
    assert.equal(context.elements.adminContent.hidden, false);
    assert.equal(context.elements.adminLoading.hidden, true);
  }
});

test("report browsing does not request schedule configuration", async () => {
  const requests = [];
  const context = runtime(["loadReports"], {
    elements: { reportError: {} }, setReportLoading() {},
    reportQueryFromFilters: () => new URLSearchParams(), updateReportUrl() {},
    authHeaders: () => ({}), renderReportPage() {}, loadReportSummary() {},
    loadReportSchedules() { throw new Error("Configuration loaded on report list"); },
    fetch: async url => { requests.push(url); return { ok: true, json: async () => ({ items: [] }) }; },
  });
  assert.equal(await context.loadReports(), true);
  assert.deepEqual(requests, ["/api/v1/daily-reports?"]);
});
