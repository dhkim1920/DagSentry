import { textElement } from "../core/dom.js";

export function applyStatusColor(element, status) {
  const colors = {
    OPEN: "red", FAILED: "red", OFFLINE: "red", DISABLED: "secondary",
    ACKNOWLEDGED: "yellow", PENDING: "yellow", RUNNING: "yellow", CLAIMED: "yellow",
    RECOVERED: "green", RESOLVED: "green", DELIVERED: "green", ONLINE: "green",
    SUCCEEDED: "green", ACTIVE: "green",
  };
  for (const color of ["red", "yellow", "green", "secondary"]) {
    element.classList.remove(`bg-${color}-lt`);
  }
  element.classList.add(`bg-${colors[status] || "secondary"}-lt`);
}

export function sourceBadge(source) {
  const badge = textElement("span", source, "source-badge badge");
  badge.dataset.source = source;
  const color = source === "RULE" ? "yellow" : source === "REUSED" ? "cyan" : "blue";
  badge.classList.add(`bg-${color}-lt`);
  return badge;
}

export function validationBadge(validationStatus) {
  const badge = textElement("span", validationStatus, "validation-badge badge");
  badge.dataset.validation = validationStatus;
  const color = ["REJECTED", "WITHDRAWN"].includes(validationStatus)
    ? "red"
    : ["PASSED", "CONFIRMED", "UPDATED"].includes(validationStatus) ? "green" : "secondary";
  badge.classList.add(`bg-${color}-lt`);
  if (validationStatus === "REJECTED") {
    badge.classList.add("is-rejected");
  }
  return badge;
}

export function diagnosisHistorySourceBadge(sourceType) {
  const badge = sourceBadge(sourceType);
  badge.textContent = {
    AI: "AI",
    RULE: "Rule",
    OPERATOR: "Operator-authored",
  }[sourceType] || sourceType;
  return badge;
}

export function diagnosisHistoryStatusBadge(status) {
  const badge = validationBadge(status);
  if (status === "REJECTED" || status === "WITHDRAWN") {
    badge.classList.add("is-rejected");
  }
  return badge;
}

export function humanClassificationLabel(value) {
  return {
    DAG_CODE: "DAG code",
    AIRFLOW_PLATFORM: "Airflow platform",
    SOURCE_DATABASE: "Source database",
    NETWORK: "Network",
    AUTHENTICATION: "Authentication",
    AUTHORIZATION: "Authorization",
    RESOURCE: "Resource",
    DATA_QUALITY: "Data quality",
    EXTERNAL_SYSTEM: "External system",
    CONFIGURATION: "Configuration",
    UNKNOWN: "Unknown",
  }[value] || value;
}

export function humanRetryLabel(value) {
  return { RETRYABLE: "Retryable", NOT_RETRYABLE: "Not retryable", UNKNOWN: "Undetermined" }[value] || value;
}
