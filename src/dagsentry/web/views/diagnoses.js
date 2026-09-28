import { contextLink, appendCell, shortId, formatTimestamp, timestampBlock, safeHttpUrl, textElement, definitionItem, copyDefinitionItem } from "../core/dom.js";
import { elements } from "../core/elements.js";
import { DIAGNOSIS_PAGE_SIZE } from "../core/constants.js";
import { state } from "../core/state.js";
import { clearSession } from "../core/session.js";
import { authHeaders, errorDetail } from "../core/api.js";
import { showAuth } from "../core/auth.js";
import { revealAdvancedFilters, diagnosisQueryFromFilters, updateDiagnosisUrl, diagnosisDetailBackHref, diagnosisHref } from "../core/router.js";
import { sourceBadge, validationBadge, diagnosisHistorySourceBadge, diagnosisHistoryStatusBadge } from "../components/badges.js";
import { renderEvidence, renderActions } from "../components/diagnosis.js";

function setDiagnosisLoading(isLoading) {
  elements.diagnosisLoading.hidden = !isLoading;
  elements.diagnosisFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.diagnosisPrevious.disabled = isLoading || state.currentDiagnosisOffset === 0;
  elements.diagnosisNext.disabled = isLoading
    || state.currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE >= state.currentDiagnosisTotal;
}

function renderDiagnosisRows(items) {
  elements.diagnosisRows.replaceChildren();
  for (const diagnosis of items) {
    const row = document.createElement("tr");
    if (diagnosis.incident_id) {
      appendCell(
        row,
        contextLink(
          `INC-${shortId(diagnosis.incident_id)}`,
          `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
        ),
      );
    } else {
      appendCell(row, textElement("span", "—"));
    }
    if (diagnosis.error_signature_id) {
      appendCell(row, contextLink(`SIG-${shortId(diagnosis.error_signature_id)}`, `/ui/?view=signatures&signature=${encodeURIComponent(diagnosis.error_signature_id)}`));
    } else {
      appendCell(row, textElement("span", "UNSIGNABLE", "muted-badge badge bg-secondary-lt"));
    }
    appendCell(row, diagnosisHistorySourceBadge(diagnosis.source_type));
    appendCell(row, textElement("span", diagnosis.root_cause || "—"));
    appendCell(row, diagnosisHistoryStatusBadge(diagnosis.status));
    appendCell(row, diagnosis.actor_identity || "—");
    appendCell(row, timestampBlock(diagnosis.created_at));
    elements.diagnosisRows.append(row);
  }
}

function renderDiagnosisPage(payload) {
  state.currentDiagnosisTotal = payload.total;
  elements.diagnosisError.hidden = true;
  elements.diagnosisEmpty.hidden = payload.items.length !== 0;
  elements.diagnosisResults.hidden = payload.items.length === 0;
  renderDiagnosisRows(payload.items);

  const start = payload.items.length ? state.currentDiagnosisOffset + 1 : 0;
  const end = state.currentDiagnosisOffset + payload.items.length;
  elements.diagnosisRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(state.currentDiagnosisOffset / DIAGNOSIS_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / DIAGNOSIS_PAGE_SIZE));
  elements.diagnosisPageLabel.textContent = `Page ${page} of ${pages}`;
  elements.diagnosisPrevious.disabled = state.currentDiagnosisOffset === 0;
  elements.diagnosisNext.disabled = state.currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE >= payload.total;
}

async function fetchDiagnosisTotal(query = "") {
  const suffix = query ? `&${query}` : "";
  const response = await fetch(`/api/v1/diagnoses/history?limit=1&offset=0${suffix}`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()).total;
}

async function loadDiagnosisSummary() {
  const summaries = [
    [elements.diagnosisTotal, ""],
    [elements.diagnosisPassedTotal, "source_type=AI"],
    [elements.diagnosisReusedTotal, "source_type=RULE"],
    [elements.diagnosisRejectedTotal, "source_type=OPERATOR"],
  ];
  for (const [element] of summaries) {
    element.textContent = "—";
  }
  const results = await Promise.allSettled(
    summaries.map(([, query]) => fetchDiagnosisTotal(query)),
  );
  for (const [index, result] of results.entries()) {
    const total = result.status === "fulfilled" ? result.value : null;
    summaries[index][0].textContent = total === null ? "—" : String(total);
  }
}

function renderDiagnosisDetail(diagnosis) {
  elements.diagnosisBreadcrumbCurrent.textContent = `DIAG-${shortId(diagnosis.id)}`;
  elements.diagnosisDetailLabels.replaceChildren(
    sourceBadge(diagnosis.source),
    validationBadge(diagnosis.validation_status),
  );
  if (diagnosis.effective) {
    elements.diagnosisDetailLabels.append(textElement("span", "Effective", "effective-badge badge bg-green-lt"));
  }
  elements.diagnosisDetailTitle.textContent = diagnosis.classification || "UNKNOWN";
  elements.diagnosisDetailRootCause.textContent = diagnosis.root_cause
    || "No Root Cause was produced.";
  elements.diagnosisDetailSource.textContent = diagnosis.source;
  elements.diagnosisDetailConfidence.textContent = diagnosis.confidence === null
    ? "Unavailable"
    : `${Math.round(diagnosis.confidence * 100)}%`;
  elements.diagnosisDetailRetry.textContent = diagnosis.retry_decision || "Unavailable";
  elements.diagnosisDetailReview.textContent = diagnosis.operator_review_required === null
    ? "Unavailable"
    : diagnosis.operator_review_required ? "Operator required" : "Not required";
  elements.diagnosisDetailCreated.textContent = formatTimestamp(diagnosis.created_at).primary;

  const rootCause = document.createElement("section");
  rootCause.className = "context-panel card card-body diagnosis-primary-section";
  rootCause.append(
    textElement("h2", "Root Cause"),
    textElement(
      "p",
      diagnosis.root_cause || "No Root Cause was produced.",
      "diagnosis-detail-cause",
    ),
  );
  if (diagnosis.validation_status === "REJECTED") {
    rootCause.append(textElement(
      "p",
      "Rejected AI attempt — retained for provenance and never used as the effective Diagnosis.",
      "rejection-note alert alert-danger",
    ));
  }
  if (diagnosis.source === "REUSED") {
    rootCause.append(textElement(
      "p",
      `Content resolved from original Diagnosis ${diagnosis.content_diagnosis_id}.`,
      "reuse-note alert alert-info",
    ));
  }

  const evidence = renderEvidence(diagnosis.evidence, "h2");
  evidence.classList.add("context-panel", "card", "card-body", "diagnosis-primary-section");
  const actions = renderActions(diagnosis.recommended_actions, "h2");
  if (actions) {
    actions.classList.add("context-panel", "card", "card-body", "diagnosis-primary-section");
  }

  const reasoning = document.createElement("section");
  reasoning.className = "diagnosis-supplement context-panel card card-body";
  reasoning.append(textElement("h3", "Reasoning metadata"));
  const reasoningFields = document.createElement("dl");
  reasoningFields.className = "context-fields";
  reasoningFields.append(
    definitionItem("Confidence reason", diagnosis.confidence_reason || "Unavailable"),
    definitionItem("Matched rule", diagnosis.matched_rule || "Unavailable"),
  );
  reasoning.append(reasoningFields);
  reasoning.append(textElement("p", "Confidence is the diagnosis score, not a measured accuracy rate.", "heading-copy text-secondary"));
  reasoning.append(textElement("p", "Validation describes automated checks, not operator confirmation of the cause. AI checks include matching cited evidence to the log.", "heading-copy text-secondary"));
  if (diagnosis.extracted_values.length) {
    const extracted = document.createElement("ul");
    extracted.className = "extracted-values";
    for (const value of diagnosis.extracted_values) {
      extracted.append(textElement("li", `${value.name}: ${value.value}`));
    }
    reasoning.append(textElement("h4", "Extracted values"), extracted);
  }
  elements.diagnosisDetailCard.replaceChildren(rootCause, evidence);
  if (actions) {
    elements.diagnosisDetailCard.append(actions);
  }
  if (diagnosis.validation_errors.length) {
    const validation = document.createElement("section");
    validation.className = "context-panel card card-body diagnosis-primary-section validation-errors";
    validation.append(textElement("h2", "Validation errors"));
    const list = document.createElement("ul");
    for (const error of diagnosis.validation_errors) {
      list.append(textElement("li", error));
    }
    validation.append(list);
    elements.diagnosisDetailCard.append(validation);
  }
  elements.diagnosisDetailCard.append(reasoning);

  const failure = diagnosis.failure;
  elements.diagnosisFailureFields.replaceChildren(
    definitionItem("Environment", failure.environment),
    definitionItem("DAG", failure.dag_id),
    definitionItem("Task", failure.task_id),
    definitionItem("DAG run", failure.dag_run_id),
    definitionItem("Try", String(failure.try_number)),
    definitionItem("Map index", String(failure.map_index)),
    definitionItem("State", failure.state),
    definitionItem("Observed", formatTimestamp(failure.observed_at).primary),
  );

  elements.diagnosisContextLinks.replaceChildren();
  elements.diagnosisLogPanel.hidden = true;
  const airflowLogUrl = safeHttpUrl(diagnosis.airflow_log_url);
  if (airflowLogUrl) {
    elements.diagnosisContextLinks.append(contextLink(
      "Open Task log in Airflow ↗",
      airflowLogUrl,
      true,
    ));
    elements.diagnosisLogPanel.hidden = false;
  }

  elements.diagnosisProvenanceFields.replaceChildren(
    copyDefinitionItem("Diagnosis ID", diagnosis.id),
    copyDefinitionItem("Failure Event ID", diagnosis.failure_event_id),
    ...(diagnosis.error_signature_id
      ? [copyDefinitionItem("Signature ID", diagnosis.error_signature_id)]
      : [definitionItem("Signature ID", "Unavailable")]),
    definitionItem("Created", formatTimestamp(diagnosis.created_at).primary),
  );
  elements.diagnosisTechnicalFields.replaceChildren(
    copyDefinitionItem("Content Diagnosis ID", diagnosis.content_diagnosis_id),
    definitionItem("Schema", `v${diagnosis.diagnosis_schema_version}`),
    definitionItem("Prompt", diagnosis.prompt_version || "Unavailable"),
    definitionItem("Rule", diagnosis.rule_version ? `v${diagnosis.rule_version}` : "Unavailable"),
  );

  elements.diagnosisLinkedIncident.replaceChildren();
  if (diagnosis.incident_id) {
    elements.diagnosisLinkedIncident.append(
      textElement("code", `INC-${shortId(diagnosis.incident_id)}`, "linked-incident-id"),
      contextLink(
        "Inspect related Incident",
        `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
      ),
    );
  } else {
    elements.diagnosisLinkedIncident.append(textElement(
      "p",
      "No linked Incident is available.",
      "aside-empty",
    ));
  }

  elements.diagnosisRelationshipLinks.replaceChildren();
  elements.diagnosisRelatedDiagnoses.hidden = true;
  if (diagnosis.reused_from_diagnosis_id) {
    elements.diagnosisRelationshipLinks.append(contextLink(
      "Open original Diagnosis",
      diagnosisHref(diagnosis.reused_from_diagnosis_id),
    ));
  }
  if (diagnosis.effective_diagnosis_id && diagnosis.effective_diagnosis_id !== diagnosis.id) {
    elements.diagnosisRelationshipLinks.append(contextLink(
      "Open effective Diagnosis",
      diagnosisHref(diagnosis.effective_diagnosis_id),
    ));
  }
  elements.diagnosisRelatedDiagnoses.hidden = !elements.diagnosisRelationshipLinks.childElementCount;

  elements.diagnosisSignatureLink.replaceChildren();
  if (diagnosis.error_signature) {
    elements.diagnosisSignatureLink.append(contextLink(
      "Explore Error Signature",
      `/ui/?view=signatures&signature=${encodeURIComponent(diagnosis.error_signature.id)}`,
    ));
  }
  if (!elements.diagnosisSignatureLink.childElementCount) {
    elements.diagnosisSignatureLink.append(textElement(
      "p",
      "No Error Signature is available.",
      "aside-empty",
    ));
  }
}

function renderSimilarDiagnoses(selectedDiagnosisId, items) {
  elements.diagnosisSimilarList.replaceChildren();
  const similar = items.filter((item) => item.id !== selectedDiagnosisId).slice(0, 5);
  if (!similar.length) {
    elements.diagnosisSimilarList.append(textElement(
      "p",
      "No other Diagnosis exists for this Error Signature.",
      "aside-empty",
    ));
    return;
  }
  for (const diagnosis of similar) {
    const row = document.createElement("article");
    row.className = "similar-diagnosis-row";
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", formatTimestamp(diagnosis.created_at).primary),
      textElement("span", `DIAG-${shortId(diagnosis.id)}`),
    );
    const confidence = diagnosis.confidence === null
      ? "Unavailable"
      : `${Math.round(diagnosis.confidence * 100)}%`;
    row.append(
      identity,
      sourceBadge(diagnosis.source),
      textElement("span", confidence, "similar-confidence"),
      validationBadge(diagnosis.validation_status),
      contextLink("View details", diagnosisHref(diagnosis.id)),
    );
    elements.diagnosisSimilarList.append(row);
  }
}

async function loadSimilarDiagnoses(diagnosis) {
  elements.diagnosisSimilarList.replaceChildren(
    textElement("p", "Loading Similar Diagnosis…", "aside-empty"),
  );
  if (!diagnosis.error_signature_id) {
    elements.diagnosisSimilarList.replaceChildren(textElement(
      "p",
      "Similar Diagnosis requires an Error Signature.",
      "aside-empty",
    ));
    return;
  }
  const params = new URLSearchParams({
    error_signature_id: diagnosis.error_signature_id,
    sort: "created_at",
    order: "desc",
    limit: "6",
    offset: "0",
  });
  try {
    const response = await fetch(`/api/v1/diagnoses?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    renderSimilarDiagnoses(diagnosis.id, (await response.json()).items);
  } catch {
    elements.diagnosisSimilarList.replaceChildren(textElement(
      "p",
      "Unable to load Similar Diagnosis.",
      "aside-empty",
    ));
  }
}

export async function loadDiagnoses() {
  setDiagnosisLoading(true);
  elements.diagnosisError.hidden = true;
  const params = diagnosisQueryFromFilters();
  updateDiagnosisUrl(params);
  try {
    const response = await fetch(`/api/v1/diagnoses/history?${params.toString()}`, {
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
    renderDiagnosisPage(await response.json());
    void loadDiagnosisSummary();
    return true;
  } catch (error) {
    elements.diagnosisResults.hidden = true;
    elements.diagnosisEmpty.hidden = true;
    elements.diagnosisError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Diagnosis History";
    elements.diagnosisError.hidden = false;
    return false;
  } finally {
    setDiagnosisLoading(false);
  }
}

export async function loadDiagnosisDetail(diagnosisId) {
  state.currentDiagnosisId = diagnosisId;
  elements.diagnosisDetailBack.href = diagnosisDetailBackHref();
  elements.diagnosisDetailLoading.hidden = false;
  elements.diagnosisDetailError.hidden = true;
  elements.diagnosisDetailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}`, {
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
    const diagnosis = await response.json();
    renderDiagnosisDetail(diagnosis);
    elements.diagnosisDetailContent.hidden = false;
    void loadSimilarDiagnoses(diagnosis);
    return true;
  } catch (error) {
    elements.diagnosisDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Diagnosis detail";
    elements.diagnosisDetailError.hidden = false;
    return false;
  } finally {
    elements.diagnosisDetailLoading.hidden = true;
  }
}

export function bindDiagnosesEvents() {
  elements.diagnosisFilterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.currentDiagnosisOffset = 0;
    await loadDiagnoses();
  });

  elements.clearDiagnosisFilters.addEventListener("click", async () => {
    elements.diagnosisFilterForm.reset();
    revealAdvancedFilters(elements.diagnosisFilterForm);
    state.currentDiagnosisOffset = 0;
    await loadDiagnoses();
  });

  elements.diagnosisPrevious.addEventListener("click", async () => {
    state.currentDiagnosisOffset = Math.max(0, state.currentDiagnosisOffset - DIAGNOSIS_PAGE_SIZE);
    await loadDiagnoses();
  });

  elements.diagnosisNext.addEventListener("click", async () => {
    if (state.currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE < state.currentDiagnosisTotal) {
      state.currentDiagnosisOffset += DIAGNOSIS_PAGE_SIZE;
      await loadDiagnoses();
    }
  });
}
