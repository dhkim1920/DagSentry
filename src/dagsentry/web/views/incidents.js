import { humanRetryLabel, humanClassificationLabel, applyStatusColor, sourceBadge, validationBadge } from "../components/badges.js";
import { contextLink, appendCell, formatTimestamp, timestampBlock, safeHttpUrl, textElement, definitionItem } from "../core/dom.js";
import { elements } from "../core/elements.js";
import { PAGE_SIZE } from "../core/constants.js";
import { state } from "../core/state.js";
import { translatedText } from "../core/i18n.js";
import { storedSession, clearSession } from "../core/session.js";
import { authHeaders, errorDetail } from "../core/api.js";
import { showAuth } from "../core/auth.js";
import { revealAdvancedFilters, queryFromFilters, updateUrl, detailBackHref } from "../core/router.js";
import { renderActions, renderDiagnosis } from "../components/diagnosis.js";

const OPERATOR_ACTIONS = {
  OPEN: [
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  ACKNOWLEDGED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  RECOVERED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Confirm the recovered Incident requires no further operational work.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this recovered Incident without further operational work.",
    },
  ],
  RESOLVED: [],
  IGNORED: [],
};

const TERMINAL_OPERATOR_ACTIONS = {
  RESOLVED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  IGNORED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
  ],
};

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

async function loadIncidentSummary(submittedParams) {
  const totals = await Promise.all(["OPEN", "ACKNOWLEDGED"].map(async (status) => {
    const params = new URLSearchParams(submittedParams);
    params.set("status", status);
    params.set("limit", "1");
    params.set("offset", "0");
    const response = await fetch(`/api/v1/incidents?${params.toString()}`, {
      headers: authHeaders(),
    });
    return response.ok ? (await response.json()).total : null;
  }));
  elements.openTotal.textContent = totals[0] === null ? "—" : String(totals[0]);
  elements.acknowledgedTotal.textContent = totals[1] === null ? "—" : String(totals[1]);
}

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

function transitionStatusLabel(transition) {
  if (
    ["RESOLVED", "IGNORED"].includes(transition.previous_status)
    && transition.status === "OPEN"
  ) {
    return "REOPENED";
  }
  return transition.status;
}

function renderTransitions(transitions) {
  elements.transitionHistory.replaceChildren();
  elements.transitionCount.textContent = String(transitions.length);
  elements.transitionHistory.classList.toggle("is-scrollable", transitions.length > 3);
  elements.transitionEmpty.hidden = transitions.length !== 0;
  const orderedTransitions = [...transitions].reverse();
  for (const transition of orderedTransitions) {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "transition-heading";
    const statusLabel = transitionStatusLabel(transition);
    heading.append(
      textElement("strong", translatedText(statusLabel)),
      textElement("time", formatTimestamp(transition.created_at).primary),
    );
    const detail = textElement(
      "p",
      `${translatedText(transition.previous_status)} → ${translatedText(statusLabel)} · ${transition.actor}`,
    );
    item.append(heading, detail);
    if (transition.reason) {
      item.append(textElement("blockquote", transition.reason));
    }
    elements.transitionHistory.append(item);
  }
}

function closeTransitionDialog() {
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
  }
  state.pendingOperatorAction = null;
  elements.transitionError.hidden = true;
}

function openTransitionDialog(action) {
  state.pendingOperatorAction = action;
  elements.transitionDialogTitle.textContent = action.label;
  elements.transitionDialogCopy.textContent = action.copy;
  elements.transitionReason.value = "";
  elements.transitionError.hidden = true;
  elements.transitionConfirm.textContent = action.label;
  elements.transitionDialog.showModal();
  elements.transitionReason.focus();
}

function renderOperatorControls(status, transitions) {
  const isOperator = ["operator", "admin"].includes(storedSession().role);
  elements.operatorControls.hidden = !isOperator;
  elements.viewerStateNote.hidden = isOperator;
  elements.operatorActions.replaceChildren();
  if (!isOperator) {
    return;
  }

  const terminalActions = TERMINAL_OPERATOR_ACTIONS[status];
  const lastTransition = transitions[transitions.length - 1];
  const canOverrideTerminal = storedSession().role === "admin"
    || (lastTransition && state.currentUser && lastTransition.actor === state.currentUser.email);
  const actions = terminalActions
    ? canOverrideTerminal ? terminalActions : []
    : OPERATOR_ACTIONS[status] || [];
  elements.operatorHelp.textContent = terminalActions
    ? canOverrideTerminal
      ? "This terminal Incident can be changed by its last operator or an Admin."
      : "Only the operator who made the terminal change or an Admin can change this Incident."
    : "Choose an explicit state change. Every change is added to the audit trail.";
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action.status === "IGNORED"
      ? "btn btn-outline-danger"
      : action.status === "RESOLVED"
        ? "btn btn-primary"
        : "btn btn-outline-secondary";
    button.textContent = action.label;
    button.addEventListener("click", () => openTransitionDialog(action));
    elements.operatorActions.append(button);
  }
}

function renderHumanDiagnosis(diagnosis) {
  const isOperator = ["operator", "admin"].includes(storedSession().role);
  elements.humanDiagnosisContent.replaceChildren();
  elements.humanDiagnosisActions.replaceChildren();
  elements.humanDiagnosisActions.hidden = !isOperator;
  elements.humanDiagnosisRevision.hidden = !diagnosis;
  if (!diagnosis) {
    elements.humanDiagnosisContent.append(
      textElement("p", translatedText("No operator-confirmed diagnosis has been published."), "aside-empty"),
    );
  } else {
    elements.humanDiagnosisRevision.textContent = `리비전 ${diagnosis.revision}`;
    const rootCause = document.createElement("div");
    rootCause.className = "human-diagnosis-root-cause";
    rootCause.append(definitionItem("근본 원인", diagnosis.root_cause));
    const assessment = document.createElement("dl");
    assessment.className = "human-diagnosis-assessment";
    assessment.append(
      definitionItem("분류", humanClassificationLabel(diagnosis.classification)),
      definitionItem("재시도 판단", humanRetryLabel(diagnosis.retry_decision)),
    );
    elements.humanDiagnosisContent.append(rootCause, assessment);
    if ((diagnosis.recommended_actions || []).length) {
      const actionsSection = document.createElement("section");
      actionsSection.className = "human-diagnosis-actions-section";
      actionsSection.append(textElement("h3", "권장 조치"));
      const actions = document.createElement("ul");
      for (const action of diagnosis.recommended_actions || []) {
        actions.append(textElement("li", action));
      }
      actionsSection.append(actions);
      elements.humanDiagnosisContent.append(actionsSection);
    }
    if (diagnosis.operator_notes) {
      elements.humanDiagnosisContent.append(definitionItem("운영자 메모", diagnosis.operator_notes));
    }
    const meta = document.createElement("dl");
    meta.className = "human-diagnosis-meta";
    meta.append(
      definitionItem("작성자", diagnosis.actor_identity),
      definitionItem("확정 시각", formatTimestamp(diagnosis.created_at).primary),
    );
    elements.humanDiagnosisContent.append(meta);
  }
  if (!isOperator) {
    return;
  }
  const publish = document.createElement("button");
  publish.type = "button";
  publish.className = "btn btn-outline-secondary";
  publish.textContent = translatedText(diagnosis ? "Edit" : "Add diagnosis");
  publish.addEventListener("click", openHumanDiagnosisDialog);
  elements.humanDiagnosisActions.append(publish);
  if (diagnosis) {
    const withdraw = document.createElement("button");
    withdraw.type = "button";
    withdraw.className = "btn btn-outline-danger";
    withdraw.textContent = translatedText("Withdraw");
    withdraw.addEventListener("click", submitHumanDiagnosisWithdrawal);
    elements.humanDiagnosisActions.append(withdraw);
  }
}

function humanRevisionLabel(revision) {
  if (revision.action === "WITHDRAW") return "철회";
  return revision.revision === 1 ? "최초 등록" : "수정";
}

function closeHumanDiagnosisDialog() {
  if (elements.humanDiagnosisDialog.open) {
    elements.humanDiagnosisDialog.close();
  }
  elements.humanDiagnosisError.hidden = true;
}

async function renderHumanDiagnosisHistory() {
  elements.humanDiagnosisHistory.replaceChildren();
  if (!state.currentIncidentId) {
    return;
  }
  const response = await fetch(
    `/api/v1/incidents/${encodeURIComponent(state.currentIncidentId)}/human-diagnoses`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    return;
  }
  const history = await response.json();
  for (const [index, revision] of history.items.entries()) {
    const item = document.createElement("li");
    const details = document.createElement("details");
    details.open = index === 0;
    const summary = document.createElement("summary");
    summary.append(
      textElement("strong", `리비전 ${revision.revision} · ${humanRevisionLabel(revision)}`),
      textElement("time", formatTimestamp(revision.created_at).primary),
    );
    details.append(summary);
    const fields = document.createElement("dl");
    fields.className = "human-diagnosis-revision-fields";
    if (revision.root_cause) {
      const rootCause = definitionItem("근본 원인", revision.root_cause);
      rootCause.className = "is-wide";
      fields.append(rootCause);
    }
    if (revision.classification) fields.append(definitionItem("분류", humanClassificationLabel(revision.classification)));
    if (revision.retry_decision) fields.append(definitionItem("재시도 판단", humanRetryLabel(revision.retry_decision)));
    if (revision.operator_notes) {
      const notes = definitionItem("운영자 메모", revision.operator_notes);
      notes.className = "is-wide";
      fields.append(notes);
    }
    if (revision.change_reason) {
      const reason = definitionItem("변경 사유", revision.change_reason);
      reason.className = "is-wide";
      fields.append(reason);
    }
    let actionsSection;
    if ((revision.recommended_actions || []).length) {
      actionsSection = document.createElement("section");
      actionsSection.className = "human-diagnosis-actions-section";
      actionsSection.append(textElement("h3", "권장 조치"));
      const list = document.createElement("ul");
      for (const action of revision.recommended_actions || []) list.append(textElement("li", action));
      actionsSection.append(list);
    }
    const author = definitionItem("작성자", revision.actor_identity);
    author.className = "is-wide";
    fields.append(author);
    details.append(fields);
    if (actionsSection) details.append(actionsSection);
    item.append(details);
    elements.humanDiagnosisHistory.append(item);
  }
}

function openHumanDiagnosisDialog() {
  elements.humanDiagnosisForm.reset();
  elements.humanDiagnosisRetry.value = "UNKNOWN";
  elements.humanDiagnosisError.hidden = true;
  elements.humanDiagnosisDialog.showModal();
  elements.humanDiagnosisRootCause.focus();
}

async function humanDiagnosisRevision() {
  if (!state.currentIncidentId) {
    return 0;
  }
  const response = await fetch(
    `/api/v1/incidents/${encodeURIComponent(state.currentIncidentId)}/human-diagnoses`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const history = await response.json();
  return history.items.length ? history.items[0].revision : 0;
}

async function submitHumanDiagnosis() {
  if (!state.currentIncidentId) {
    return;
  }
  const actions = elements.humanDiagnosisActionsInput.value
    .split("\n").map((value) => value.trim()).filter(Boolean);
  const body = {
    expected_revision: await humanDiagnosisRevision(),
    classification: elements.humanDiagnosisClassification.value,
    root_cause: elements.humanDiagnosisRootCause.value.trim(),
    recommended_actions: actions,
    retry_decision: elements.humanDiagnosisRetry.value,
    operator_notes: elements.humanDiagnosisNotes.value.trim() || null,
    change_reason: elements.humanDiagnosisChangeReason.value.trim() || null,
  };
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(state.currentIncidentId)}/human-diagnoses`,
      { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(body) },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    closeHumanDiagnosisDialog();
    if (await loadIncidentDetail(state.currentIncidentId)) {
      showDetailFeedback(translatedText("Operator-confirmed diagnosis published."));
    }
  } catch (error) {
    elements.humanDiagnosisError.textContent = error instanceof Error ? error.message : translatedText("Unable to publish diagnosis");
    elements.humanDiagnosisError.hidden = false;
  }
}

async function submitHumanDiagnosisWithdrawal() {
  if (!state.currentIncidentId) {
    return;
  }
  const reason = window.prompt(translatedText("Reason for withdrawal:"));
  if (!reason) {
    return;
  }
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(state.currentIncidentId)}/human-diagnoses/withdraw`,
      { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: await humanDiagnosisRevision(), reason }) },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    if (await loadIncidentDetail(state.currentIncidentId)) {
      showDetailFeedback(translatedText("Operator-confirmed diagnosis withdrawn."));
    }
  } catch (error) {
    showDetailFeedback(error instanceof Error ? error.message : translatedText("Unable to withdraw diagnosis"));
  }
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

function renderIncidentDetail(payload) {
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
  void renderHumanDiagnosisHistory();
  elements.detailContent.hidden = false;
}

export async function loadIncidents() {
  setLoading(true);
  elements.error.hidden = true;
  const params = queryFromFilters();
  updateUrl(params);
  try {
    const response = await fetch(`/api/v1/incidents?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderPage(await response.json());
    await loadIncidentSummary(params);
    return true;
  } catch (error) {
    elements.results.hidden = true;
    elements.empty.hidden = true;
    elements.error.textContent = error instanceof Error ? error.message : "Unable to load incidents";
    elements.error.hidden = false;
    return false;
  } finally {
    setLoading(false);
  }
}

export async function loadIncidentDetail(incidentId) {
  elements.detailBack.href = detailBackHref();
  elements.detailLoading.hidden = false;
  elements.detailError.hidden = true;
  elements.detailFeedback.hidden = true;
  elements.detailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderIncidentDetail(await response.json());
    return true;
  } catch (error) {
    elements.detailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Incident detail";
    elements.detailError.hidden = false;
    return false;
  } finally {
    elements.detailLoading.hidden = true;
  }
}

function setTransitionSubmitting(isSubmitting) {
  elements.transitionForm.setAttribute("aria-busy", String(isSubmitting));
  elements.transitionClose.disabled = isSubmitting;
  elements.transitionCancel.disabled = isSubmitting;
  elements.transitionConfirm.disabled = isSubmitting;
}

async function submitOperatorTransition() {
  if (!state.pendingOperatorAction || !state.currentIncidentId || !state.currentIncidentStatus) {
    return;
  }

  const action = state.pendingOperatorAction;
  const incidentId = state.currentIncidentId;
  const body = {
    status: action.status,
    expected_status: state.currentIncidentStatus,
  };
  const reason = elements.transitionReason.value.trim();
  if (reason) {
    body.reason = reason;
  }

  setTransitionSubmitting(true);
  elements.transitionError.hidden = true;
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/status`,
      {
        method: "PATCH",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (response.status === 409) {
      const detail = await errorDetail(response);
      closeTransitionDialog();
      if (await loadIncidentDetail(incidentId)) {
        showDetailFeedback(`${detail}. The Incident was refreshed; reconsider the current state.`);
      }
      return;
    }
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return;
      }
      throw new Error(detail);
    }

    closeTransitionDialog();
    if (await loadIncidentDetail(incidentId)) {
      showDetailFeedback(`Incident changed to ${action.status}.`);
    }
  } catch (error) {
    elements.transitionError.textContent = error instanceof Error
      ? error.message
      : "Unable to change the Incident state";
    elements.transitionError.hidden = false;
  } finally {
    setTransitionSubmitting(false);
  }
}

export function bindIncidentsEvents() {
  elements.transitionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitOperatorTransition();
  });

  elements.transitionClose.addEventListener("click", closeTransitionDialog);

  elements.transitionCancel.addEventListener("click", closeTransitionDialog);

  elements.transitionDialog.addEventListener("cancel", (event) => {
    if (elements.transitionConfirm.disabled) {
      event.preventDefault();
      return;
    }
    state.pendingOperatorAction = null;
    elements.transitionError.hidden = true;
  });

  elements.humanDiagnosisForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitHumanDiagnosis();
  });

  elements.humanDiagnosisClose.addEventListener("click", closeHumanDiagnosisDialog);

  elements.humanDiagnosisCancel.addEventListener("click", closeHumanDiagnosisDialog);

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
