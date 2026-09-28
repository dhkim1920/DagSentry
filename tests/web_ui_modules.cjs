const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const web = path.resolve(__dirname, "../src/dagsentry/web");

async function modules() {
  const forms = new Map();
  const context = vm.createContext({
    URLSearchParams,
    localStorage: { getItem: () => null },
    navigator: { language: "en" },
    window: { location: { search: "" }, matchMedia: () => ({ matches: false }) },
    document: {
      querySelector: selector => forms.get(selector) || {},
      querySelectorAll: () => [],
    },
    FormData: class {
      constructor(form) { this.form = form; }
      entries() { return this.form.elements.filter(item => item.name).map(item => [item.name, item.value]); }
    },
    history: { replaceState: (_state, _title, url) => { context.url = url; } },
  });
  const cache = new Map();
  function load(filename) {
    if (!cache.has(filename)) {
      cache.set(filename, new vm.SourceTextModule(fs.readFileSync(filename, "utf8"), {
        context, identifier: filename,
      }));
    }
    return cache.get(filename);
  }
  const entry = load(path.join(web, "app.js"));
  // Link the real entry point, including every named import. Do not bootstrap UI/network.
  await entry.link((specifier, referencing) => load(path.resolve(path.dirname(referencing.identifier), specifier)));
  const router = cache.get(path.join(web, "core/router.js"));
  await router.evaluate();
  return { context, router: router.namespace, state: cache.get(path.join(web, "core/state.js")).namespace.state, elements: cache.get(path.join(web, "core/elements.js")).namespace.elements };
}

function form(values) {
  const elements = Object.entries(values).map(([name, value]) => ({ name, value, defaultValue: "", tagName: "INPUT" }));
  return { elements, querySelector: () => ({ open: false, querySelectorAll: () => elements }) };
}

test("native module imports link and URL restoration resets all view offsets", async () => {
  const { context, router, state, elements } = await modules();
  elements.filterForm = form({ status: "", dag_id: "stale" });
  elements.signatureFilterForm = form({ classification: "stale" });
  elements.diagnosisFilterForm = form({ source_type: "stale" });
  elements.reportFilterForm = form({ environment: "stale" });
  for (const offset of ["-20", "invalid", "20"]) {
    context.window.location.search = `?offset=${offset}&occurrence_offset=${offset}`;
    router.setFiltersFromUrl(); router.setSignatureFiltersFromUrl();
    router.setDiagnosisFiltersFromUrl(); router.setReportFiltersFromUrl();
    const expected = offset === "20" ? 20 : 0;
    for (const key of ["currentOffset", "currentSignatureOffset", "currentOccurrenceOffset", "currentDiagnosisOffset", "currentReportOffset"]) {
      assert.equal(state[key], expected, key);
    }
  }
  assert.equal(elements.filterForm.elements[0].value, "OPEN");
  assert.equal(elements.filterForm.elements[1].value, "");
  assert.equal(elements.reportFilterForm.elements[0].value, "");
});

test("native query helpers preserve allowlists and list/detail navigation parameters", async () => {
  const { context, router, elements } = await modules();
  elements.diagnosisFilterForm = form({ source_type: " AI ", error_signature_id: "sig", sort: "ignored", unexpected: "ignored" });
  const query = router.diagnosisQueryFromFilters(20);
  assert.equal(query.toString(), "source_type=AI&error_signature_id=sig&limit=20&offset=20");
  router.updateDiagnosisUrl(query);
  assert.equal(context.url, "/ui/?source_type=AI&error_signature_id=sig&offset=20&view=diagnoses");
  router.updateUrl(new URLSearchParams("limit=20&offset=0"));
  assert.equal(context.url, "/ui/");
  context.window.location.search = "?view=signatures&signature=sig&occurrence_offset=20&environment=prod&offset=40";
  assert.equal(router.signatureDetailBackHref(), "/ui/?view=signatures&environment=prod&offset=40");
  assert.equal(router.occurrenceQueryFromUrl(20).toString(), "environment=prod&limit=20&offset=20");
});
