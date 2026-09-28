import { elements } from "./elements.js";
import { PAGE_SIZE, SIGNATURE_PAGE_SIZE, OCCURRENCE_PAGE_SIZE, DIAGNOSIS_PAGE_SIZE, REPORT_PAGE_SIZE } from "./constants.js";
import { state } from "./state.js";

function offsetFromUrl(params, name = "offset") {
  const offset = Number.parseInt(params.get(name) || "0", 10);
  return Number.isFinite(offset) && offset >= 0 ? offset : 0;
}

function restoreFilters(form, defaults = {}, advanced = true) {
  const params = new URLSearchParams(window.location.search);
  for (const control of form.elements) {
    if (control.name) {
      control.value = params.get(control.name) ?? defaults[control.name] ?? "";
    }
  }
  if (advanced) revealAdvancedFilters(form);
  return params;
}

function filterQuery(form, limit, offset, allowedNames = null) {
  const params = new URLSearchParams();
  for (const [name, rawValue] of new FormData(form).entries()) {
    if (allowedNames && !allowedNames.includes(name)) continue;
    const value = String(rawValue).trim();
    if (value) params.set(name, value);
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return params;
}

function replaceFilterUrl(params, view = null) {
  const visible = new URLSearchParams(params);
  if (view) visible.set("view", view);
  visible.delete("limit");
  if (visible.get("offset") === "0") visible.delete("offset");
  const query = visible.toString();
  history.replaceState({}, "", query ? `/ui/?${query}` : "/ui/");
}

export function revealAdvancedFilters(form) {
  const details = form.querySelector(".advanced-filters");
  details.open = Array.from(details.querySelectorAll("input, select")).some((control) => {
    const defaultValue = control.tagName === "SELECT"
      ? control.options[0].value
      : control.defaultValue;
    return control.value !== defaultValue;
  });
}

export function setFiltersFromUrl() {
  const params = restoreFilters(elements.filterForm, { status: "OPEN", sort: "last_failure_at", order: "desc" });
  state.currentOffset = offsetFromUrl(params);
}

export function setSignatureFiltersFromUrl() {
  const params = restoreFilters(elements.signatureFilterForm, { sort: "last_seen_at", order: "desc" });
  state.currentSignatureOffset = offsetFromUrl(params);
  state.currentOccurrenceOffset = offsetFromUrl(params, "occurrence_offset");
}

export function setDiagnosisFiltersFromUrl() {
  const params = restoreFilters(elements.diagnosisFilterForm, { sort: "created_at", order: "desc" });
  state.currentDiagnosisOffset = offsetFromUrl(params);
}

export function setReportFiltersFromUrl() {
  const params = restoreFilters(elements.reportFilterForm, {}, false);
  state.currentReportOffset = offsetFromUrl(params);
}

export function queryFromFilters(offset = state.currentOffset) {
  return filterQuery(elements.filterForm, PAGE_SIZE, offset);
}

export function updateUrl(params) {
  replaceFilterUrl(params);
}

export function signatureQueryFromFilters(offset = state.currentSignatureOffset) {
  return filterQuery(elements.signatureFilterForm, SIGNATURE_PAGE_SIZE, offset);
}

export function updateSignatureUrl(params) {
  replaceFilterUrl(params, "signatures");
}

export function signatureDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "signatures");
  params.delete("signature");
  params.delete("occurrence_offset");
  return `/ui/?${params.toString()}`;
}

export function occurrenceQueryFromUrl(offset = state.currentOccurrenceOffset) {
  const source = new URLSearchParams(window.location.search);
  const params = new URLSearchParams();
  for (const name of ["environment", "dag_id", "task_id", "date_from", "date_to"]) {
    if (source.has(name)) {
      params.set(name, source.get(name));
    }
  }
  params.set("limit", String(OCCURRENCE_PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

export function diagnosisQueryFromFilters(offset = state.currentDiagnosisOffset) {
  return filterQuery(elements.diagnosisFilterForm, DIAGNOSIS_PAGE_SIZE, offset, ["source_type", "error_signature_id", "date_from", "date_to"]);
}

export function updateDiagnosisUrl(params) {
  replaceFilterUrl(params, "diagnoses");
}

export function diagnosisDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "diagnoses");
  params.delete("diagnosis");
  return `/ui/?${params.toString()}`;
}

export function reportQueryFromFilters(offset = state.currentReportOffset) {
  return filterQuery(elements.reportFilterForm, REPORT_PAGE_SIZE, offset);
}

export function updateReportUrl(params) {
  replaceFilterUrl(params, "reports");
}

export function reportDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "reports");
  params.delete("report");
  return `/ui/?${params.toString()}`;
}

export function diagnosisHref(diagnosisId) {
  const params = new URLSearchParams(window.location.search);
  params.delete("incident");
  params.delete("signature");
  params.delete("occurrence_offset");
  params.set("view", "diagnoses");
  params.set("diagnosis", diagnosisId);
  return `/ui/?${params.toString()}`;
}

export function detailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.delete("incident");
  const query = params.toString();
  return query ? `/ui/?${query}` : "/ui/";
}

export function adminPageFromUrl() {
  const section = new URLSearchParams(window.location.search).get("section");
  return ["users", "connections", "reports", "audit"].includes(section) ? section : "users";
}

export function reportHref(reportId) {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "reports");
  params.set("report", reportId);
  return `/ui/?${params.toString()}`;
}
