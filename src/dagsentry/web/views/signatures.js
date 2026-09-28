import { beginViewRequest, beginTrendRequest } from "../core/requests.js";
import { humanRetryLabel, humanClassificationLabel, applyStatusColor, sourceBadge } from "../components/badges.js";
import { contextLink, appendCell, shortId, formatTimestamp, timestampBlock, textElement, definitionItem, copyDefinitionItem } from "../core/dom.js";
import { elements } from "../core/elements.js";
import { SIGNATURE_PAGE_SIZE, OCCURRENCE_PAGE_SIZE } from "../core/constants.js";
import { state } from "../core/state.js";
import { translatedText } from "../core/i18n.js";
import { apiJson } from "../core/api.js";
import { handleApiError } from "../core/auth.js";
import { revealAdvancedFilters, signatureQueryFromFilters, updateSignatureUrl, signatureDetailBackHref, occurrenceQueryFromUrl, diagnosisHref } from "../core/router.js";

function setSignatureLoading(isLoading) {
  elements.signatureLoading.hidden = !isLoading;
  elements.signatureFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.signaturePrevious.disabled = isLoading || state.currentSignatureOffset === 0;
  elements.signatureNext.disabled = isLoading
    || state.currentSignatureOffset + SIGNATURE_PAGE_SIZE >= state.currentSignatureTotal;
}

function signatureLabel(signature) {
  return signature.exception_class
    || signature.vendor_error_code
    || signature.operator_type
    || "Unclassified Error Signature";
}

function renderSignatureRows(items) {
  elements.signatureRows.replaceChildren();
  for (const signature of items) {
    const row = document.createElement("tr");
    row.dataset.signatureId = signature.id;

    const identity = document.createElement("div");
    identity.className = "signature-identity";
    identity.append(
      textElement("strong", signatureLabel(signature)),
      textElement("span", signature.normalized_message || "No normalized message"),
    );
    appendCell(row, identity);

    const correlation = document.createElement("div");
    correlation.className = "task-identity";
    correlation.append(
      textElement("strong", signature.operator_type || "Unknown operator"),
      textElement(
        "span",
        [signature.exception_class, signature.vendor_error_code].filter(Boolean).join(" · ")
          || "No exception or vendor code",
      ),
    );
    appendCell(row, correlation);
    appendCell(row, String(signature.incident_count), "numeric");
    appendCell(row, String(signature.failure_count), "numeric");
    appendCell(row, timestampBlock(signature.first_seen_at));
    appendCell(row, timestampBlock(signature.last_seen_at));

    const inspect = document.createElement("a");
    const params = new URLSearchParams(window.location.search);
    params.set("view", "signatures");
    params.set("signature", signature.id);
    params.delete("occurrence_offset");
    inspect.className = "inspect-link btn btn-outline-secondary btn-sm";
    inspect.href = `/ui/?${params.toString()}`;
    inspect.textContent = "Explore";
    inspect.setAttribute("aria-label", `Explore ${signatureLabel(signature)}`);
    appendCell(row, inspect, "action-cell");
    elements.signatureRows.append(row);
  }
}

function renderSignaturePage(payload) {
  state.currentSignatureTotal = payload.total;
  elements.signatureTotal.textContent = String(payload.total);
  elements.signatureError.hidden = true;
  elements.signatureEmpty.hidden = payload.items.length !== 0;
  elements.signatureResults.hidden = payload.items.length === 0;
  renderSignatureRows(payload.items);

  const start = payload.items.length ? state.currentSignatureOffset + 1 : 0;
  const end = state.currentSignatureOffset + payload.items.length;
  elements.signatureRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(state.currentSignatureOffset / SIGNATURE_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / SIGNATURE_PAGE_SIZE));
  elements.signaturePageLabel.textContent = `Page ${page} of ${pages}`;
  elements.signaturePrevious.disabled = state.currentSignatureOffset === 0;
  elements.signatureNext.disabled = state.currentSignatureOffset + SIGNATURE_PAGE_SIZE >= payload.total;
}

function renderLatestSignatureDiagnosis(diagnosis) {
  elements.signatureLatestDiagnosis.replaceChildren();
  if (!diagnosis) {
    elements.signatureLatestDiagnosis.append(textElement(
      "p",
      "No validated original Diagnosis is available.",
      "aside-empty",
    ));
    return;
  }

  const labels = document.createElement("div");
  labels.className = "diagnosis-labels signature-diagnosis-labels";
  labels.append(
    sourceBadge(diagnosis.source),
    textElement("span", diagnosis.classification, "classification-badge badge bg-blue-lt"),
  );
  const rootCause = textElement(
    "p",
    diagnosis.root_cause || "No Root Cause was produced.",
    "signature-root-cause",
  );
  const facts = document.createElement("dl");
  facts.className = "signature-diagnosis-facts";
  facts.append(
    definitionItem("Confidence", `${Math.round(diagnosis.confidence * 100)}%`),
    definitionItem("Retry", diagnosis.retry_decision),
    definitionItem("Created", formatTimestamp(diagnosis.created_at).primary),
  );
  elements.signatureLatestDiagnosis.append(
    labels,
    rootCause,
    facts,
    contextLink("Inspect Diagnosis", diagnosisHref(diagnosis.id)),
  );
}

function renderSignatureIdentity(signature) {
  elements.signatureBreadcrumbCurrent.textContent = signatureLabel(signature);
  elements.signatureDetailTitle.textContent = signatureLabel(signature);
  elements.signatureDetailMessage.textContent = signature.normalized_message
    || "No normalized message was retained.";
  elements.signatureDetailFailures.textContent = String(signature.failure_count);
  elements.signatureDetailIncidents.textContent = String(signature.incident_count);
  elements.signatureDetailFirstSeen.textContent = formatTimestamp(signature.first_seen_at).primary;
  elements.signatureDetailLastSeen.textContent = formatTimestamp(signature.last_seen_at).primary;
  elements.signatureCanonicalFields.replaceChildren(
    copyDefinitionItem("Fingerprint", signature.fingerprint),
    definitionItem("Fingerprint version", String(signature.fingerprint_version)),
    definitionItem("Operator", signature.operator_type || "Unavailable"),
    definitionItem("Exception", signature.exception_class || "Unavailable"),
    definitionItem("Vendor code", signature.vendor_error_code || "Unavailable"),
    definitionItem("Stack frame", signature.application_stack_frame || "Unavailable"),
  );
  renderLatestSignatureDiagnosis(signature.latest_validated_diagnosis);
  const operatorDiagnoses = signature.operator_diagnoses || [];
  elements.signatureOperatorDiagnoses.replaceChildren();
  if (!operatorDiagnoses.length) {
    elements.signatureOperatorDiagnoses.append(textElement("p", "등록된 과거 운영자 진단이 없습니다.", "aside-empty"));
    return;
  }
  for (const diagnosis of operatorDiagnoses) {
    const item = document.createElement("article");
    item.className = "diagnosis-card card card-body operator-diagnosis-card";
    const meta = document.createElement("div");
    meta.className = "diagnosis-card-meta";
    const metaIncident = document.createElement("div");
    const incidentLink = contextLink(
      `INC-${shortId(diagnosis.incident_id)}`,
      `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
    );
    incidentLink.className = "operator-diagnosis-id";
    metaIncident.append(incidentLink);
    const metaAuthor = document.createElement("div");
    metaAuthor.append(
      textElement("time", formatTimestamp(diagnosis.created_at).primary),
      textElement("span", diagnosis.actor_identity, "muted-label"),
    );
    meta.append(metaIncident, metaAuthor);
    const body = document.createElement("div");
    body.className = "operator-diagnosis-body";
    const fields = document.createElement("dl");
    fields.className = "diagnosis-card-fields";
    fields.append(
      definitionItem("근본 원인", diagnosis.root_cause),
      definitionItem("분류", humanClassificationLabel(diagnosis.classification)),
      definitionItem("재시도 판단", humanRetryLabel(diagnosis.retry_decision)),
    );
    body.append(fields);
    const recommendedActions = diagnosis.recommended_actions || [];
    if (recommendedActions.length) {
      const actionsSection = document.createElement("section");
      actionsSection.className = "operator-diagnosis-recommendations";
      actionsSection.append(textElement("h3", "권장 조치"));
      const actions = document.createElement("ul");
      for (const action of recommendedActions) actions.append(textElement("li", action));
      actionsSection.append(actions);
      body.append(actionsSection);
    }
    const footer = document.createElement("div");
    footer.className = "operator-diagnosis-actions";
    const incidentButton = contextLink(
      "인시던트 보기",
      `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
    );
    incidentButton.className = "btn btn-outline-secondary operator-diagnosis-button";
    footer.append(incidentButton);
    item.append(meta, body, footer);
    elements.signatureOperatorDiagnoses.append(item);
  }
}

function addUtcDays(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function trendQueryForSignature(signature, days = state.currentSignatureTrendDays) {
  const dateTo = signature.last_seen_at.slice(0, 10);
  const dateFrom = addUtcDays(dateTo, -(days - 1));
  return new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
}

function updateSignatureTrendControls() {
  elements.signatureTrend7.setAttribute("aria-pressed", String(state.currentSignatureTrendDays === 7));
  elements.signatureTrend7.classList.toggle("active", state.currentSignatureTrendDays === 7);
  elements.signatureTrend30.setAttribute("aria-pressed", String(state.currentSignatureTrendDays === 30));
  elements.signatureTrend30.classList.toggle("active", state.currentSignatureTrendDays === 30);
}

function renderSignatureTrend(payload) {
  const maximum = Math.max(0, ...payload.items.map((item) => item.failure_count));
  const step = 10 ** Math.floor(Math.log10(maximum || 1));
  const ceiling = Math.max(2, Math.ceil(maximum / (2 * step)) * 2 * step);
  elements.signatureTrendRange.textContent = `${payload.date_from} – ${payload.date_to}`;
  elements.signatureTrendTotal.textContent = String(payload.items.reduce((sum, item) => sum + item.failure_count, 0));
  elements.signatureTrendEmpty.hidden = maximum !== 0;
  elements.signatureTrendReadout.textContent = translatedText("Point to a day or use the arrow keys to inspect its failure count.");
  elements.signatureTrendYAxis.replaceChildren(...[ceiling, ceiling / 2, 0].map(value => textElement("span", String(value))));
  elements.signatureTrendDates.replaceChildren();
  elements.signatureTrendPoints.replaceChildren();
  window.TablerSparkline.getInstance(elements.signatureTrendSparkline)?.dispose();
  new window.TablerSparkline(elements.signatureTrendSparkline, {
    type: "bar", values: payload.items.map(item => item.failure_count),
    width: 700, height: 180, min: 0, max: ceiling,
    barGap: payload.items.length <= 7 ? 48 : 10, barRadius: 2, animation: 0,
  });
  const last = payload.items.length - 1;
  const ticks = new Set([0, Math.round(last / 4), Math.round(last / 2), Math.round(last * 3 / 4), last]);
  for (const [index, item] of payload.items.entries()) {
    const date = textElement("time", "");
    date.dateTime = item.date;
    if (payload.items.length <= 7 || ticks.has(index)) {
      date.textContent = item.date.slice(5);
    }
    if (payload.items.length <= 7 && index !== 0 && index !== 3 && index !== last) {
      date.className = "trend-date-intermediate";
    }
    elements.signatureTrendDates.append(date);
    const label = `${item.date} · ${item.failure_count} ${translatedText("Failures")}`;
    const point = textElement("button", "", "trend-point btn");
    point.type = "button";
    point.tabIndex = index === last ? 0 : -1;
    point.setAttribute("aria-label", label);
    const inspect = () => { elements.signatureTrendReadout.textContent = label; };
    point.addEventListener("pointerenter", inspect);
    point.addEventListener("focus", () => {
      for (const button of elements.signatureTrendPoints.children) button.tabIndex = -1;
      point.tabIndex = 0;
      inspect();
    });
    point.addEventListener("click", () => { point.focus(); inspect(); });
    point.addEventListener("keydown", event => {
      const next = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: last }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      elements.signatureTrendPoints.children[Math.max(0, Math.min(last, next))].focus();
    });
    elements.signatureTrendPoints.append(point);
  }
}

function renderSignatureOccurrences(payload) {
  state.currentOccurrenceTotal = payload.total;
  elements.signatureOccurrenceRows.replaceChildren();
  elements.signatureOccurrenceEmpty.hidden = payload.items.length !== 0;
  elements.signatureOccurrenceTable.hidden = payload.items.length === 0;
  for (const occurrence of payload.items) {
    const row = document.createElement("tr");
    const incident = document.createElement("div");
    incident.className = "task-identity";
    const status = textElement("strong", occurrence.incident_status);
    status.className = "status-badge badge";
    status.dataset.status = occurrence.incident_status;
    applyStatusColor(status, occurrence.incident_status);
    incident.append(status, textElement("span", `INC-${shortId(occurrence.incident_id)}`));
    appendCell(row, incident);

    const task = document.createElement("div");
    task.className = "task-identity";
    task.append(textElement("strong", occurrence.dag_id), textElement("span", occurrence.task_id));
    appendCell(row, task);
    appendCell(row, occurrence.environment);

    const taskTry = document.createElement("div");
    taskTry.className = "task-identity";
    taskTry.append(
      textElement("strong", `Try ${occurrence.try_number} · ${occurrence.failure_state}`),
      textElement("span", `${occurrence.dag_run_id} · map ${occurrence.map_index}`),
    );
    appendCell(row, taskTry);
    appendCell(row, timestampBlock(occurrence.observed_at));

    const inspect = document.createElement("a");
    inspect.className = "inspect-link btn btn-outline-secondary btn-sm";
    inspect.href = `/ui/?incident=${encodeURIComponent(occurrence.incident_id)}`;
    inspect.textContent = "Inspect";
    inspect.setAttribute("aria-label", `Inspect Incident ${occurrence.incident_id}`);
    appendCell(row, inspect, "action-cell");
    elements.signatureOccurrenceRows.append(row);
  }

  const start = payload.items.length ? state.currentOccurrenceOffset + 1 : 0;
  const end = state.currentOccurrenceOffset + payload.items.length;
  elements.signatureOccurrenceRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(state.currentOccurrenceOffset / OCCURRENCE_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / OCCURRENCE_PAGE_SIZE));
  elements.occurrencePageLabel.textContent = `Page ${page} of ${pages}`;
  elements.occurrencePrevious.disabled = state.currentOccurrenceOffset === 0;
  elements.occurrenceNext.disabled = state.currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE >= payload.total;
}

export async function loadSignatures() {
  const request = beginViewRequest();
  setSignatureLoading(true);
  elements.signatureError.hidden = true;
  const params = signatureQueryFromFilters();
  updateSignatureUrl(params);
  try {
    const response = await apiJson(`/api/v1/error-signatures?${params.toString()}`, {
      signal: request.signal,
    });
    if (!request.isCurrent()) return false;
    renderSignaturePage(response);
    return true;
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    elements.signatureResults.hidden = true;
    elements.signatureEmpty.hidden = true;
    elements.signatureError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signatures";
    elements.signatureError.hidden = false;
    return false;
  } finally {
    if (request.isCurrent()) {
      setSignatureLoading(false);
    }
  }
}

export async function loadSignatureDetail(signatureId) {
  const request = beginViewRequest();
  if (state.currentSignatureId !== signatureId) {
    state.currentSignatureTrendDays = 7;
  }
  state.currentSignatureId = signatureId;
  state.currentSignature = null;
  elements.signatureTrend7.disabled = false;
  elements.signatureTrend30.disabled = false;
  elements.signatureTrend.setAttribute("aria-busy", "false");
  elements.signatureDetailBack.href = signatureDetailBackHref();
  elements.signatureDetailLoading.hidden = false;
  elements.signatureDetailError.hidden = true;
  elements.signatureTrendError.hidden = true;
  elements.signatureDetailContent.hidden = true;
  try {
    const response = await apiJson(`/api/v1/error-signatures/${encodeURIComponent(signatureId)}`, {
      signal: request.signal,
    });
    if (!request.isCurrent()) return false;
    const signature = response;
    state.currentSignature = signature;
    updateSignatureTrendControls();
    const occurrenceParams = occurrenceQueryFromUrl();
    const trendParams = trendQueryForSignature(signature);
    const [occurrenceResponse, trendResponse] = await Promise.all([
      apiJson(
        `/api/v1/error-signatures/${encodeURIComponent(signatureId)}/occurrences?${occurrenceParams.toString()}`,
        { signal: request.signal },
      ),
      apiJson(
        `/api/v1/error-signatures/${encodeURIComponent(signatureId)}/trend?${trendParams.toString()}`,
        { signal: request.signal },
      ),
    ]);
    if (!request.isCurrent()) return false;

    renderSignatureIdentity(signature);
    renderSignatureOccurrences(occurrenceResponse);
    renderSignatureTrend(trendResponse);
    elements.signatureDetailContent.hidden = false;
    return true;
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    elements.signatureDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signature detail";
    elements.signatureDetailError.hidden = false;
    return false;
  } finally {
    if (request.isCurrent()) {
      elements.signatureDetailLoading.hidden = true;
    }
  }
}

async function refreshSignatureTrend(days) {
  const request = beginTrendRequest();
  if (!state.currentSignatureId || !state.currentSignature) {
    return;
  }
  elements.signatureTrendError.hidden = true;
  elements.signatureTrend.setAttribute("aria-busy", "true");
  const previousReadout = elements.signatureTrendReadout.textContent;
  elements.signatureTrendReadout.textContent = translatedText("Loading trend…");
  elements.signatureTrend7.disabled = true;
  elements.signatureTrend30.disabled = true;
  try {
    const trendParams = trendQueryForSignature(state.currentSignature, days);
    const response = await apiJson(
      `/api/v1/error-signatures/${encodeURIComponent(state.currentSignatureId)}/trend?${trendParams.toString()}`,
      { signal: request.signal },
    );
    if (!request.isCurrent()) return false;
    renderSignatureTrend(response);
    state.currentSignatureTrendDays = days;
    updateSignatureTrendControls();
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return false;
    elements.signatureTrendReadout.textContent = previousReadout;
    elements.signatureTrendError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signature trend";
    elements.signatureTrendError.hidden = false;
  } finally {
    if (request.isCurrent()) {
      elements.signatureTrend.setAttribute("aria-busy", "false");
      elements.signatureTrend7.disabled = false;
      elements.signatureTrend30.disabled = false;
    }
  }
}

async function changeOccurrencePage(offset) {
  state.currentOccurrenceOffset = Math.max(0, offset);
  const params = new URLSearchParams(window.location.search);
  if (state.currentOccurrenceOffset === 0) {
    params.delete("occurrence_offset");
  } else {
    params.set("occurrence_offset", String(state.currentOccurrenceOffset));
  }
  history.replaceState({}, "", `/ui/?${params.toString()}`);
  if (state.currentSignatureId) {
    await loadSignatureDetail(state.currentSignatureId);
  }
}

export function bindSignaturesEvents() {
  elements.signatureFilterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.currentSignatureOffset = 0;
    await loadSignatures();
  });

  elements.clearSignatureFilters.addEventListener("click", async () => {
    elements.signatureFilterForm.reset();
    revealAdvancedFilters(elements.signatureFilterForm);
    state.currentSignatureOffset = 0;
    await loadSignatures();
  });

  elements.signaturePrevious.addEventListener("click", async () => {
    state.currentSignatureOffset = Math.max(0, state.currentSignatureOffset - SIGNATURE_PAGE_SIZE);
    await loadSignatures();
  });

  elements.signatureNext.addEventListener("click", async () => {
    if (state.currentSignatureOffset + SIGNATURE_PAGE_SIZE < state.currentSignatureTotal) {
      state.currentSignatureOffset += SIGNATURE_PAGE_SIZE;
      await loadSignatures();
    }
  });

  elements.occurrencePrevious.addEventListener("click", async () => {
    await changeOccurrencePage(state.currentOccurrenceOffset - OCCURRENCE_PAGE_SIZE);
  });

  elements.occurrenceNext.addEventListener("click", async () => {
    if (state.currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE < state.currentOccurrenceTotal) {
      await changeOccurrencePage(state.currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE);
    }
  });

  elements.signatureTrend7.addEventListener("click", async () => {
    await refreshSignatureTrend(7);
  });

  elements.signatureTrend30.addEventListener("click", async () => {
    await refreshSignatureTrend(30);
  });
}
