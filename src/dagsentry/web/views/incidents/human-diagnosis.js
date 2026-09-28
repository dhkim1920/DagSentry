import { currentViewRequest } from "../../core/requests.js";
import { humanRetryLabel, humanClassificationLabel } from "../../components/badges.js";
import { formatTimestamp, textElement, definitionItem } from "../../core/dom.js";
import { elements } from "../../core/elements.js";
import { state } from "../../core/state.js";
import { translatedText } from "../../core/i18n.js";
import { storedSession } from "../../core/session.js";
import { apiJson, apiRequest } from "../../core/api.js";
import { handleApiError } from "../../core/auth.js";

let refreshIncident;
let showFeedback;

export function renderHumanDiagnosis(diagnosis) {
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

export async function renderHumanDiagnosisHistory(request) {
  elements.humanDiagnosisHistory.replaceChildren();
  if (!state.currentIncidentId) {
    return;
  }
  let history;
  try {
    history = await apiJson(
      `/api/v1/incidents/${encodeURIComponent(state.currentIncidentId)}/human-diagnoses`,
      { signal: request.signal },
    );
    if (!request.isCurrent()) return;
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return;
    elements.humanDiagnosisHistory.append(textElement("li", "Unable to load diagnosis history."));
    return;
  }
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

async function humanDiagnosisRevision(incidentId, request) {
  if (!incidentId) {
    return 0;
  }
  const history = await apiJson(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/human-diagnoses`,
    { signal: request.signal },
  );
  return history.items.length ? history.items[0].revision : 0;
}

async function submitHumanDiagnosis() {
  const request = currentViewRequest();
  const incidentId = state.currentIncidentId;
  if (!incidentId) {
    return;
  }
  const actions = elements.humanDiagnosisActionsInput.value
    .split("\n").map((value) => value.trim()).filter(Boolean);
  const body = {
    classification: elements.humanDiagnosisClassification.value,
    root_cause: elements.humanDiagnosisRootCause.value.trim(),
    recommended_actions: actions,
    retry_decision: elements.humanDiagnosisRetry.value,
    operator_notes: elements.humanDiagnosisNotes.value.trim() || null,
    change_reason: elements.humanDiagnosisChangeReason.value.trim() || null,
  };
  try {
    body.expected_revision = await humanDiagnosisRevision(incidentId, request);
    if (!request.isCurrent()) return;
    await apiRequest(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/human-diagnoses`,
      { signal: request.signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    );
    if (!request.isCurrent()) return;
    closeHumanDiagnosisDialog();
    await refreshIncident(incidentId, translatedText("Operator-confirmed diagnosis published."));
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return;
    elements.humanDiagnosisError.textContent = error instanceof Error ? error.message : translatedText("Unable to publish diagnosis");
    elements.humanDiagnosisError.hidden = false;
  }
}

async function submitHumanDiagnosisWithdrawal() {
  const request = currentViewRequest();
  const incidentId = state.currentIncidentId;
  if (!incidentId) {
    return;
  }
  const reason = window.prompt(translatedText("Reason for withdrawal:"));
  if (!reason) {
    return;
  }
  try {
    await apiRequest(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/human-diagnoses/withdraw`,
      { signal: request.signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: await humanDiagnosisRevision(incidentId, request), reason }) },
    );
    if (!request.isCurrent()) return;
    await refreshIncident(incidentId, translatedText("Operator-confirmed diagnosis withdrawn."));
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return;
    showFeedback(error instanceof Error ? error.message : translatedText("Unable to withdraw diagnosis"));
  }
}

export function bindHumanDiagnosisEvents(onChanged, onFeedback) {
  refreshIncident = onChanged;
  showFeedback = onFeedback;
  elements.humanDiagnosisForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitHumanDiagnosis();
  });

  elements.humanDiagnosisClose.addEventListener("click", closeHumanDiagnosisDialog);

  elements.humanDiagnosisCancel.addEventListener("click", closeHumanDiagnosisDialog);
}
