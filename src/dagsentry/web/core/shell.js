import { elements } from "./elements.js";
import { setSidebarVisibility } from "./sidebar.js";
import { translatedText } from "./i18n.js";
import { storedSession } from "./session.js";
import { adminPageFromUrl } from "./router.js";

export function showConnectedView(view) {
  const session = storedSession();
  setSidebarVisibility(true);
  const adminView = view === "admin";
  const signatureView = view === "signatures" || view === "signature-detail";
  const diagnosisView = view === "diagnoses" || view === "diagnosis-detail";
  const reportView = view === "reports" || view === "report-detail";
  const incidentView = !signatureView && !diagnosisView && !reportView && !adminView;
  const titles = {
    dashboard: "DagSentry — Incident response",
    detail: "DagSentry — Incident detail",
    signatures: "DagSentry — Error patterns",
    "signature-detail": "DagSentry — Error Signature detail",
    diagnoses: "DagSentry — Diagnosis History",
    "diagnosis-detail": "DagSentry — Diagnosis detail",
    reports: "DagSentry — Daily Reports",
    "report-detail": "DagSentry — Daily Report detail",
    admin: "DagSentry — Settings",
  };
  document.title = translatedText(titles[view] || "DagSentry");
  elements.authPanel.hidden = true;
  elements.dashboard.hidden = view !== "dashboard";
  elements.detail.hidden = view !== "detail";
  elements.signatureDashboard.hidden = view !== "signatures";
  elements.signatureDetail.hidden = view !== "signature-detail";
  elements.diagnosisDashboard.hidden = view !== "diagnoses";
  elements.diagnosisDetail.hidden = view !== "diagnosis-detail";
  elements.reportDashboard.hidden = view !== "reports";
  elements.reportDetail.hidden = view !== "report-detail";
  elements.adminDashboard.hidden = !adminView;
  elements.adminNavigation.hidden = false;
  elements.incidentsNav.classList.toggle("active", incidentView);
  elements.signaturesNav.classList.toggle("active", signatureView);
  elements.diagnosesNav.classList.toggle("active", diagnosisView);
  elements.reportsNav.classList.toggle("active", reportView);
  for (const link of elements.adminLinks) {
    link.parentElement.hidden = session.role !== "admin" && link.dataset.settingsLink !== "reports";
    const active = adminView && link.dataset.settingsLink === adminPageFromUrl();
    link.classList.toggle("active", active);
    link.parentElement.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (adminView) elements.adminNavigation.open = true;
  elements.adminNavigation.classList.toggle("active", adminView);
  for (const [nav, active] of [
    [elements.incidentsNav, incidentView],
    [elements.signaturesNav, signatureView],
    [elements.diagnosesNav, diagnosisView],
    [elements.reportsNav, reportView],
  ]) {
    nav.parentElement.classList.toggle("active", active);
    if (active) {
      nav.setAttribute("aria-current", "page");
    } else {
      nav.removeAttribute("aria-current");
    }
  }
  elements.disconnect.hidden = false;
  elements.sessionStatus.textContent = `${session.displayName} · ${session.role}`;
}
