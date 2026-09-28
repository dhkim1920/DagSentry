import { diagnosisHref } from "../core/router.js";
import { textElement, definitionItem } from "../core/dom.js";
import { sourceBadge, validationBadge } from "./badges.js";

export function renderEvidence(evidence, headingTag = "h4") {
  const section = document.createElement("section");
  section.className = "diagnosis-section";
  section.append(textElement(headingTag, "Evidence"));
  if (!evidence.length) {
    section.append(textElement("p", "No log Evidence was retained for this Diagnosis.", "muted-copy"));
    return section;
  }
  const list = document.createElement("ol");
  list.className = "evidence-list";
  for (const item of evidence) {
    const entry = document.createElement("li");
    entry.value = item.line_id;
    entry.append(
      textElement("span", String(item.line_id), "evidence-line-id"),
      textElement("code", item.text),
    );
    list.append(entry);
  }
  section.append(list);
  return section;
}

export function renderActions(actions, headingTag = "h4") {
  if (!actions.length) {
    return null;
  }
  const section = document.createElement("section");
  section.className = "diagnosis-section";
  section.append(textElement(headingTag, "Recommended actions"));
  const list = document.createElement("ul");
  list.className = "recommendation-list";
  for (const action of actions) {
    list.append(textElement("li", action));
  }
  section.append(list);
  return section;
}

export function renderDiagnosis(diagnosis, includeDetailLink = true) {
  const card = document.createElement("article");
  card.className = "diagnosis-card card card-body";
  if (diagnosis.effective) {
    card.classList.add("is-effective");
  }
  if (diagnosis.validation_status === "REJECTED") {
    card.classList.add("is-rejected");
  }

  const heading = document.createElement("div");
  heading.className = "diagnosis-heading";
  const labels = document.createElement("div");
  labels.className = "diagnosis-labels";
  labels.append(sourceBadge(diagnosis.source));
  labels.append(validationBadge(diagnosis.validation_status));
  if (diagnosis.effective) {
    labels.append(textElement("span", "Effective", "effective-badge badge bg-green-lt"));
  }
  const rootCause = textElement(
    "h3",
    diagnosis.root_cause || "No Root Cause was produced",
    "diagnosis-root-cause",
  );
  heading.append(labels, rootCause);
  card.append(heading);

  if (diagnosis.validation_status === "REJECTED") {
    card.append(textElement(
      "p",
      "Rejected AI attempt — retained for provenance and never used as the effective Diagnosis.",
      "rejection-note alert alert-danger",
    ));
  }
  if (diagnosis.source === "REUSED") {
    card.append(textElement(
      "p",
      `Content resolved from original Diagnosis ${diagnosis.content_diagnosis_id}.`,
      "reuse-note alert alert-info",
    ));
  }

  const facts = document.createElement("dl");
  facts.className = "diagnosis-facts";
  const confidence = diagnosis.confidence === null
    ? "Unavailable"
    : `${Math.round(diagnosis.confidence * 100)}%`;
  facts.append(
    definitionItem("Classification", diagnosis.classification || "UNKNOWN"),
    definitionItem("Confidence", confidence),
    definitionItem("Retry", diagnosis.retry_decision || "UNKNOWN"),
    definitionItem(
      "Review",
      diagnosis.operator_review_required === null
        ? "Unavailable"
        : diagnosis.operator_review_required ? "Operator required" : "Not required",
    ),
  );
  card.append(facts);
  card.append(textElement("p", "Confidence is the diagnosis score, not a measured accuracy rate.", "heading-copy text-secondary"));
  card.append(textElement("p", "Validation describes automated checks, not operator confirmation of the cause. AI checks include matching cited evidence to the log.", "heading-copy text-secondary"));
  card.append(renderEvidence(diagnosis.evidence));

  const actions = renderActions(diagnosis.recommended_actions);
  if (actions) {
    card.append(actions);
  }
  if (diagnosis.validation_errors.length) {
    const validation = document.createElement("section");
    validation.className = "diagnosis-section validation-errors";
    validation.append(textElement("h4", "Validation errors"));
    const list = document.createElement("ul");
    for (const error of diagnosis.validation_errors) {
      list.append(textElement("li", error));
    }
    validation.append(list);
    card.append(validation);
  }

  const provenance = document.createElement("footer");
  provenance.className = "diagnosis-provenance";
  const versionDetails = document.createElement("details");
  versionDetails.className = "diagnosis-version-details";
  versionDetails.append(
    textElement("summary", "Version details"),
    textElement(
      "code",
      `schema v${diagnosis.diagnosis_schema_version} · prompt ${diagnosis.prompt_version || "—"} · rule ${diagnosis.rule_version || "—"}`,
    ),
  );
  provenance.append(
    textElement("code", diagnosis.id),
    versionDetails,
  );
  if (includeDetailLink) {
    const inspect = document.createElement("a");
    inspect.className = "diagnosis-detail-link";
    inspect.href = diagnosisHref(diagnosis.id);
    inspect.textContent = "Inspect Diagnosis →";
    provenance.append(inspect);
  }
  card.append(provenance);
  return card;
}
