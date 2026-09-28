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
    RULE: "규칙",
    OPERATOR: "운영자",
  }[sourceType] || sourceType;
  return badge;
}

export function diagnosisHistoryStatusBadge(status) {
  const badge = validationBadge(status);
  badge.textContent = {
    PASSED: "검증 통과",
    REJECTED: "거부",
    CONFIRMED: "확정",
    UPDATED: "수정됨",
    WITHDRAWN: "철회",
  }[status] || status;
  if (status === "REJECTED" || status === "WITHDRAWN") {
    badge.classList.add("is-rejected");
  }
  return badge;
}

export function humanClassificationLabel(value) {
  return {
    DAG_CODE: "DAG 코드",
    AIRFLOW_PLATFORM: "Airflow 플랫폼",
    SOURCE_DATABASE: "원본 데이터베이스",
    NETWORK: "네트워크",
    AUTHENTICATION: "인증",
    AUTHORIZATION: "권한",
    RESOURCE: "리소스",
    DATA_QUALITY: "데이터 품질",
    EXTERNAL_SYSTEM: "외부 시스템",
    CONFIGURATION: "설정",
    UNKNOWN: "알 수 없음",
  }[value] || value;
}

export function humanRetryLabel(value) {
  return { RETRYABLE: "재시도 가능", NOT_RETRYABLE: "재시도 불가", UNKNOWN: "판단 보류" }[value] || value;
}
