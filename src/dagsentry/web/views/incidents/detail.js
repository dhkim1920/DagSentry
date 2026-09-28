import { beginViewRequest } from "../../core/requests.js";
import { applyStatusColor, sourceBadge, validationBadge } from "../../components/badges.js";
import { contextLink, formatTimestamp, safeHttpUrl, textElement, definitionItem } from "../../core/dom.js";
import { elements } from "../../core/elements.js";
import { state } from "../../core/state.js";
import { apiJson } from "../../core/api.js";
import { handleApiError } from "../../core/auth.js";
import { detailBackHref } from "../../core/router.js";
import { renderActions, renderDiagnosis } from "../../components/diagnosis.js";
import { renderTransitions, renderOperatorControls, bindTransitionsEvents } from "./transitions.js";
import { renderHumanDiagnosis, renderHumanDiagnosisHistory, bindHumanDiagnosisEvents } from "./human-diagnosis.js";

function renderSignature(signature) {
  const panel = document.createElement("details");
  panel.className = "signature-panel card";
  const summary = document.createElement("summary");
  summary.textContent = "Error Signature";
  const content = document.createElement("dl");
  content.className = "signature-fields";
  content.append(
    definitionItem("Fingerprint", signature.fingerprint),
    definitionItem("Version", String(signature.fingerprint_version)),
    definitionItem("Exception", signature.exception_class || "Unavailable"),
    definitionItem("Vendor code", signature.vendor_error_code || "Unavailable"),
    definitionItem("Operator", signature.operator_type || "Unavailable"),
    definitionItem("Message", signature.normalized_message || "Unavailable"),
  );
  const explore = document.createElement("a");
  explore.className = "signature-explore-link";
  explore.href = `/ui/?view=signatures&signature=${encodeURIComponent(signature.id)}`;
  explore.textContent = "Explore recurring occurrences →";
  panel.append(summary, content, explore);
  return panel;
}

function renderFailure(failure, index) {
  const card = document.createElement("article");
  card.className = "failure-card card";
  const heading = document.createElement("header");
  heading.className = "failure-heading card-header";
  const title = document.createElement("div");
  title.append(
    textElement("p", `Occurrence ${index + 1}`, "table-kicker"),
    textElement("h3", `Try ${failure.try_number} · ${failure.state}`),
  );
  if (failure.is_initial_failure) {
    title.append(textElement("span", state.currentLanguage === "ko" ? "최초 실패" : "Initial failure", "status-pill"));
  }
  if (failure.is_final_failure) {
    title.append(textElement("span", state.currentLanguage === "ko" ? "최종 실패 알림 대상" : "Final failure notification", "status-pill"));
  }
  const metadata = document.createElement("div");
  metadata.className = "failure-metadata";
  metadata.append(
    textElement("span", formatTimestamp(failure.observed_at).primary),
    textElement("span", `run ${failure.dag_run_id}`),
    textElement("span", `map ${failure.map_index}`),
  );
  heading.append(title, metadata);
  card.append(heading);

  const airflowLogUrl = safeHttpUrl(failure.airflow_log_url);
  if (airflowLogUrl) {
    const logLink = document.createElement("a");
    logLink.className = "airflow-link";
    logLink.href = airflowLogUrl;
    logLink.target = "_blank";
    logLink.rel = "noreferrer noopener";
    logLink.textContent = "Open Task log in Airflow ↗";
    card.append(logLink);
  }

  const diagnoses = document.createElement("section");
  diagnoses.className = "diagnosis-stack";
  diagnoses.append(textElement("h3", "Diagnosis attempts", "stack-title"));
  if (!failure.diagnoses.length) {
    diagnoses.append(textElement("p", "Diagnosis has not been persisted yet.", "muted-copy"));
  } else {
    for (const diagnosis of failure.diagnoses) {
      diagnoses.append(renderDiagnosis(diagnosis));
    }
  }
  card.append(diagnoses);
  if (failure.error_signature) {
    card.append(renderSignature(failure.error_signature));
  } else {
    card.append(textElement(
      "p",
      "Unsignable Failure — no stable Error Signature was available for correlation.",
      "unsignable-note alert alert-warning",
    ));
  }
  return card;
}

function showDetailFeedback(message) {
  elements.detailFeedback.textContent = message;
  elements.detailFeedback.hidden = false;
  elements.detailFeedback.focus();
}

function latestFailureDiagnosis(failures) {
  const latestFailure = failures.at(-1);
  return latestFailure?.diagnoses.find((diagnosis) => (
    diagnosis.effective && diagnosis.validation_status === "PASSED"
  )) || null;
}

function renderCurrentDiagnosis(payload) {
  const content = elements.currentDiagnosisContent;
  content.replaceChildren();
  elements.currentDiagnosisPanel.open = !payload.current_human_diagnosis;
  elements.humanDiagnosisPanel.open = Boolean(payload.current_human_diagnosis);
  elements.incidentDiagnoses.prepend(payload.current_human_diagnosis
    ? elements.humanDiagnosisPanel : elements.currentDiagnosisPanel);
  const diagnosis = latestFailureDiagnosis(payload.failures);
  const latestFailure = payload.failures.at(-1);
  if (!diagnosis) {
    content.append(textElement("p", "No validated diagnosis is available for the latest failure. Review its task log or earlier attempts in the history.", "heading-copy text-secondary"));
  } else {
    const labels = document.createElement("div");
    labels.className = "diagnosis-labels";
    labels.append(sourceBadge(diagnosis.source), validationBadge(diagnosis.validation_status));
    content.append(labels);
    content.append(textElement("h3", diagnosis.root_cause || "No Root Cause was produced", "diagnosis-root-cause"));
    const facts = document.createElement("dl");
    facts.className = "diagnosis-facts";
    facts.append(
      definitionItem("Last activity", formatTimestamp(latestFailure.observed_at).primary),
      definitionItem("Retry", diagnosis.retry_decision || "UNKNOWN"),
      definitionItem("Review", diagnosis.operator_review_required === null
        ? "Unavailable" : diagnosis.operator_review_required ? "Operator required" : "Not required"),
    );
    content.append(facts);
    const actions = renderActions(diagnosis.recommended_actions);
    if (actions) content.append(actions);
    const evidence = document.createElement("details");
    evidence.className = "diagnosis-technical-details";
    evidence.append(textElement("summary", "Evidence and diagnosis details"), renderDiagnosis(diagnosis));
    content.append(evidence);
  }
  const logUrl = safeHttpUrl(latestFailure?.airflow_log_url);
  if (latestFailure?.airflow_log_url && logUrl) {
    const link = contextLink("Open Task log in Airflow ↗", logUrl);
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    content.append(link);
  }
}

function renderIncidentDetail(payload, request) {
  const incident = payload.incident;
  state.currentIncidentId = incident.id;
  state.currentIncidentStatus = incident.status;
  elements.detailStatus.textContent = incident.status.replaceAll("_", " ");
  elements.detailStatus.dataset.status = incident.status;
  applyStatusColor(elements.detailStatus, incident.status);
  elements.detailStateHelp.textContent = {
    OPEN: "This incident is awaiting investigation.",
    ACKNOWLEDGED: "An operator has started investigating this incident.",
    RECOVERED: "The task has recovered. Confirm whether follow-up is complete before resolving the incident.",
    RESOLVED: "An operator has closed this incident after confirming remediation.",
    IGNORED: "An operator has closed this incident without further action.",
  }[incident.status];
  elements.detailEnvironment.textContent = incident.environment;
  elements.detailTitle.textContent = incident.dag_id;
  elements.detailTask.textContent = incident.task_id;
  elements.detailFailureCount.textContent = String(incident.failure_count);
  elements.detailFirstSeen.textContent = formatTimestamp(incident.first_failure_at).primary;
  elements.detailLastSeen.textContent = formatTimestamp(incident.last_failure_at).primary;
  elements.detailIncidentId.textContent = incident.id;
  elements.detailSignatureLink.hidden = !incident.error_signature_id;
  if (incident.error_signature_id) {
    elements.detailSignatureLink.href = `/ui/?view=signatures&signature=${encodeURIComponent(incident.error_signature_id)}`;
  } else {
    elements.detailSignatureLink.removeAttribute("href");
  }
  elements.detailFailures.replaceChildren();
  for (const [index, failure] of payload.failures.entries()) {
    elements.detailFailures.append(renderFailure(failure, index));
  }
  renderTransitions(payload.transitions);
  renderOperatorControls(incident.status, payload.transitions);
  renderHumanDiagnosis(payload.current_human_diagnosis);
  renderCurrentDiagnosis(payload);
  void renderHumanDiagnosisHistory(request);
  elements.detailContent.hidden = false;
}

export async function loadIncidentDetail(incidentId) {
  const request = beginViewRequest();
  elements.detailBack.href = detailBackHref();
  elements.detailLoading.hidden = false;
  elements.detailError.hidden = true;
  elements.detailFeedback.hidden = true;
  elements.detailContent.hidden = true;
  try {
    const response = await apiJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
      signal: request.signal,
    });
    if (!request.isCurrent()) return false;
    renderIncidentDetail(response, request);
    return true;
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    elements.detailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Incident detail";
    elements.detailError.hidden = false;
    return false;
  } finally {
    if (request.isCurrent()) {
      elements.detailLoading.hidden = true;
    }
  }
}

async function refreshIncident(incidentId, message) {
  if (await loadIncidentDetail(incidentId)) showDetailFeedback(message);
}

export function bindIncidentDetailEvents() {
  bindTransitionsEvents(refreshIncident);
  bindHumanDiagnosisEvents(refreshIncident, showDetailFeedback);
}
