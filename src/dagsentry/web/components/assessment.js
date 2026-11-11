import { textElement } from "../core/dom.js";

export function evidenceBasisLabel(assessment) {
  if (!assessment) return "Historical diagnosis · assessment unavailable";
  return {
    DIRECT: "Direct evidence",
    INFERRED: "Inferred cause",
    UNDETERMINED: "Unable to determine",
  }[assessment.basis];
}

export function renderAssessment(assessment) {
  const section = document.createElement("section");
  section.className = "diagnosis-section diagnosis-assessment";
  section.append(textElement("h3", "Cause assessment"));
  section.append(textElement("p", evidenceBasisLabel(assessment)));
  if (!assessment) return section;
  section.append(textElement("p", assessment.reason));
  section.append(textElement("p", "Evidence support is assessed by the diagnosis, not independently confirmed by an operator.", "text-secondary"));
  for (const [key, title] of [
    ["facts", "Observed facts"],
    ["candidate_causes", "Candidate causes"],
    ["missing_information", "Missing information"],
    ["next_checks", "Next checks"],
  ]) {
    if (!assessment[key].length) continue;
    const list = document.createElement("ul");
    for (const item of assessment[key]) list.append(textElement("li", item));
    section.append(textElement("h4", title), list);
  }
  return section;
}
