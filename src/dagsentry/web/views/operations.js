import { elements } from "../core/elements.js";
import { apiJson, ApiError } from "../core/api.js";
import { handleApiError } from "../core/auth.js";
import { textElement, formatTimestamp, definitionItem } from "../core/dom.js";
import { translatedText } from "../core/i18n.js";

function observedTime(value, hasSnapshot) {
  return value ? formatTimestamp(value).primary : translatedText(hasSnapshot ? "Not observed yet" : "Unknown");
}

function renderOperations(snapshot, apiStatus = "UNKNOWN", databaseStatus = "UNKNOWN") {
  elements.operationsServices.replaceChildren();
  elements.operationsQueue.replaceChildren();
  elements.operationsObserved.textContent = observedTime(snapshot?.observed_at, false);
  const states = {
    ONLINE: "Online", OFFLINE: "Offline", UNKNOWN: "No observation",
    READY: "Ready", UNAVAILABLE: "Unavailable",
  };
  for (const [service, title, status] of [
    ["api", "API", snapshot?.api_status || apiStatus],
    ["database", "Database", snapshot?.database_status || databaseStatus],
    ["worker", "Worker", snapshot?.worker.status || "UNKNOWN"],
    ["scheduler", "Scheduler", snapshot?.scheduler.status || "UNKNOWN"],
  ]) {
    const column = textElement("div", "", "col-12 col-md-6 col-xl-3");
    column.dataset.service = service;
    const card = textElement("div", "", "card card-body h-100");
    const color = ["ONLINE", "READY"].includes(status) ? "success"
      : ["OFFLINE", "UNAVAILABLE"].includes(status) ? "danger" : "secondary";
    const label = status === "UNKNOWN" && !snapshot ? "Unknown" : states[status];
    const badge = textElement("span", label, `badge bg-${color}-lt align-self-start`);
    badge.dataset.status = status;
    card.append(textElement("h3", title, "card-title"), badge);
    if (service === "worker" || service === "scheduler") {
      const observation = snapshot?.[service];
      const details = document.createElement("dl");
      details.className = "canonical-fields";
      details.append(
        definitionItem("Last heartbeat", observedTime(observation?.last_heartbeat_at, Boolean(snapshot))),
        definitionItem(service === "worker" ? "Last completed diagnosis job" : "Last successful report run", observedTime(observation?.last_success_at, Boolean(snapshot))),
      );
      if (service === "worker") details.prepend(definitionItem("Online instances", String(snapshot?.worker.online_instances ?? "—")));
      card.append(details);
    }
    if (service === "api") card.append(textElement("p", snapshot?.version || "—", "mt-3"));
    column.append(card);
    elements.operationsServices.append(column);
  }
  for (const [key, label] of [
    ["pending", "Pending jobs"], ["ready", "Ready now"], ["delayed", "Waiting for retry"],
    ["processing", "Processing jobs"], ["stale_processing", "Stale processing locks"],
    ["dead", "Failed jobs"], ["completed", "Completed jobs"],
    ["oldest_ready_age_seconds", "Oldest ready wait"],
  ]) {
    let value = snapshot?.queue[key] ?? "—";
    if (snapshot && key === "oldest_ready_age_seconds") {
      value = snapshot.queue.oldest_ready_at
        ? `${Math.floor(value)} ${translatedText("seconds")}` : translatedText("No ready jobs");
    }
    const item = definitionItem(label, String(value));
    item.className = "col-12 col-sm-6 col-xl-3";
    item.dataset.metric = key;
    if (snapshot?.queue[key] > 0 && ["dead", "stale_processing"].includes(key)) item.classList.add("text-warning");
    elements.operationsQueue.append(item);
  }
}

export async function loadOperations(request) {
  elements.operationsError.hidden = true;
  renderOperations(null);
  try {
    const snapshot = await apiJson("/api/v1/admin/operations", { signal: request.signal });
    if (!request.isCurrent()) return false;
    renderOperations(snapshot);
    return true;
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    let apiStatus = error instanceof ApiError && error.message === "Operational status is unavailable"
      ? "ONLINE" : "UNKNOWN";
    let databaseStatus = "UNKNOWN";
    try {
      const health = await apiJson("/health/ready", { signal: request.signal });
      apiStatus = "ONLINE";
      databaseStatus = health.status === "ready" ? "READY" : "UNKNOWN";
    } catch (healthError) {
      if (healthError instanceof ApiError && healthError.status === 503
        && healthError.message === "Database is not ready") {
        apiStatus = "ONLINE";
        databaseStatus = "UNAVAILABLE";
      }
    }
    if (!request.isCurrent()) return false;
    renderOperations(null, apiStatus, databaseStatus);
    elements.operationsError.textContent = translatedText("Operational snapshot unavailable. Counts are unknown. Check service logs and migrations, then refresh.");
    elements.operationsError.hidden = false;
    return false;
  }
}
