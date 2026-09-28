import { beginViewRequest } from "../../core/requests.js";
import { applyStatusColor } from "../../components/badges.js";
import { appendCell, timestampBlock, textElement } from "../../core/dom.js";
import { elements } from "../../core/elements.js";
import { PAGE_SIZE } from "../../core/constants.js";
import { state } from "../../core/state.js";
import { apiJson } from "../../core/api.js";
import { handleApiError } from "../../core/auth.js";
import { revealAdvancedFilters, queryFromFilters, updateUrl } from "../../core/router.js";

function setLoading(isLoading) {
  elements.loading.hidden = !isLoading;
  elements.filterForm.setAttribute("aria-busy", String(isLoading));
  elements.refreshIncidents.disabled = isLoading;
  elements.previous.disabled = isLoading || state.currentOffset === 0;
  elements.next.disabled = isLoading || state.currentOffset + PAGE_SIZE >= state.currentTotal;
}

function renderRows(items) {
  elements.rows.replaceChildren();
  for (const incident of items) {
    const row = document.createElement("tr");
    row.dataset.incidentId = incident.id;

    const status = document.createElement("span");
    status.className = "status-badge badge";
    status.dataset.status = incident.status;
    applyStatusColor(status, incident.status);
    status.textContent = incident.status.replaceAll("_", " ");
    appendCell(row, status);

    const incidentIdentity = document.createElement("div");
    incidentIdentity.className = "task-identity incident-error-summary";
    incidentIdentity.append(
      textElement("strong", incident.exception_class || incident.normalized_message || "Error summary unavailable"),
    );
    if (incident.exception_class && incident.normalized_message && incident.normalized_message !== incident.exception_class) {
      incidentIdentity.append(textElement("span", incident.normalized_message));
    }
    appendCell(row, incidentIdentity);
    appendCell(row, incident.environment);

    const taskIdentity = document.createElement("div");
    taskIdentity.className = "task-identity";
    taskIdentity.append(
      textElement("strong", incident.dag_id),
      textElement("span", incident.task_id),
    );
    appendCell(row, taskIdentity);

    const failures = document.createElement("span");
    failures.className = "failure-count";
    failures.textContent = String(incident.failure_count);
    appendCell(row, failures, "numeric");
    appendCell(row, timestampBlock(incident.last_failure_at));

    const inspect = document.createElement("a");
    const detailParams = new URLSearchParams(window.location.search);
    detailParams.set("incident", incident.id);
    inspect.className = "inspect-link btn btn-outline-secondary btn-sm";
    inspect.href = `/ui/?${detailParams.toString()}`;
    inspect.textContent = "Inspect";
    inspect.setAttribute("aria-label", `Inspect ${incident.dag_id} ${incident.task_id} Incident`);
    appendCell(row, inspect, "action-cell");
    for (const [index, title] of [[2, "Environment"], [3, "DAG / Task"], [4, "Failures"], [5, "Last activity"]]) {
      const label = textElement("span", title, "incident-cell-label");
      label.setAttribute("aria-hidden", "true");
      row.children[index].append(label);
    }
    elements.rows.append(row);
  }
}

function renderPage(payload) {
  state.currentTotal = payload.total;
  elements.total.textContent = String(payload.total);
  elements.error.hidden = true;
  elements.empty.hidden = payload.items.length !== 0;
  elements.results.hidden = payload.items.length === 0;
  renderRows(payload.items);

  const start = payload.items.length ? state.currentOffset + 1 : 0;
  const end = state.currentOffset + payload.items.length;
  elements.range.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(state.currentOffset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / PAGE_SIZE));
  elements.pageLabel.textContent = `Page ${page} of ${pages}`;
  elements.previous.disabled = state.currentOffset === 0;
  elements.next.disabled = state.currentOffset + PAGE_SIZE >= payload.total;
}

async function loadIncidentSummary(submittedParams, request) {
  const totals = await Promise.allSettled(["OPEN", "ACKNOWLEDGED"].map(async (status) => {
    const params = new URLSearchParams(submittedParams);
    params.set("status", status);
    params.set("limit", "1");
    params.set("offset", "0");
    const response = await apiJson(`/api/v1/incidents?${params.toString()}`, {
      signal: request.signal,
    });
    return response.total;
  }));
  if (!request.isCurrent()) return;
  for (const result of totals) {
    if (result.status === "rejected" && handleApiError(result.reason)) return;
  }
  const values = totals.map(result => result.status === "fulfilled" ? String(result.value) : "—");
  elements.openTotal.textContent = values[0];
  elements.acknowledgedTotal.textContent = values[1];
}

export async function loadIncidents() {
  const request = beginViewRequest();
  setLoading(true);
  elements.error.hidden = true;
  const params = queryFromFilters();
  updateUrl(params);
  try {
    const response = await apiJson(`/api/v1/incidents?${params.toString()}`, {
      signal: request.signal,
    });
    if (!request.isCurrent()) return false;
    renderPage(response);
    await loadIncidentSummary(params, request);
    return request.isCurrent();
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    elements.results.hidden = true;
    elements.empty.hidden = true;
    elements.error.textContent = error instanceof Error ? error.message : "Unable to load incidents";
    elements.error.hidden = false;
    return false;
  } finally {
    if (request.isCurrent()) {
      setLoading(false);
    }
  }
}

export function bindListEvents() {
  elements.filterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.currentOffset = 0;
    await loadIncidents();
  });

  elements.refreshIncidents.addEventListener("click", async () => {
    await loadIncidents();
  });

  elements.clearFilters.addEventListener("click", async () => {
    elements.filterForm.reset();
    revealAdvancedFilters(elements.filterForm);
    document.querySelector("#status-filter").value = "OPEN";
    state.currentOffset = 0;
    await loadIncidents();
  });

  elements.previous.addEventListener("click", async () => {
    state.currentOffset = Math.max(0, state.currentOffset - PAGE_SIZE);
    await loadIncidents();
  });

  elements.next.addEventListener("click", async () => {
    if (state.currentOffset + PAGE_SIZE < state.currentTotal) {
      state.currentOffset += PAGE_SIZE;
      await loadIncidents();
    }
  });
}
