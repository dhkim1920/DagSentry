import { cancelViewRequests } from "./requests.js";
import { elements } from "./elements.js";
import { loadIncidents } from "../views/incidents/list.js";
import { loadIncidentDetail } from "../views/incidents/detail.js";
import { state } from "./state.js";
import { sidebarMedia, sidebarCollapsed, applySidebarState, setSidebarCollapsed } from "./sidebar.js";
import { storedSession } from "./session.js";
import { showPasswordChange } from "./auth.js";
import { showConnectedView } from "./shell.js";
import { setFiltersFromUrl, setSignatureFiltersFromUrl, setDiagnosisFiltersFromUrl, setReportFiltersFromUrl, adminPageFromUrl } from "./router.js";
import { loadSignatures, loadSignatureDetail } from "../views/signatures.js";
import { loadDiagnoses, loadDiagnosisDetail } from "../views/diagnoses.js";
import { loadReports, loadReportDetail } from "../views/reports.js";
import { loadAdminDashboard } from "../views/admin.js";

export async function loadCurrentView() {
  cancelViewRequests();
  if (state.currentUser?.must_change_password) {
    showPasswordChange();
    return false;
  }
  const params = new URLSearchParams(window.location.search);
  const reportId = params.get("report");
  if (reportId) {
    showConnectedView("report-detail");
    return loadReportDetail(reportId);
  }
  const diagnosisId = params.get("diagnosis");
  if (diagnosisId) {
    showConnectedView("diagnosis-detail");
    return loadDiagnosisDetail(diagnosisId);
  }
  const signatureId = params.get("signature");
  if (signatureId) {
    showConnectedView("signature-detail");
    return loadSignatureDetail(signatureId);
  }
  const incidentId = params.get("incident");
  if (incidentId) {
    showConnectedView("detail");
    return loadIncidentDetail(incidentId);
  }
  if (params.get("view") === "signatures") {
    state.currentSignatureId = null;
    showConnectedView("signatures");
    return loadSignatures();
  }
  if (params.get("view") === "diagnoses") {
    state.currentDiagnosisId = null;
    showConnectedView("diagnoses");
    return loadDiagnoses();
  }
  if (params.get("view") === "reports") {
    state.currentReportId = null;
    showConnectedView("reports");
    return loadReports();
  }
  if (params.get("view") === "admin"
    && (storedSession().role === "admin" || adminPageFromUrl() === "reports")) {
    showConnectedView("admin");
    return loadAdminDashboard();
  }
  state.currentIncidentId = null;
  state.currentIncidentStatus = null;
  showConnectedView("dashboard");
  return loadIncidents();
}

export function bindNavigationEvents() {
  elements.sidebarToggle.addEventListener("click", () => {
    setSidebarCollapsed(!sidebarCollapsed);
  });

  sidebarMedia.addEventListener("change", applySidebarState);

  elements.primaryNavigation.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebarMedia.matches && !sidebarCollapsed) {
      setSidebarCollapsed(true);
      elements.sidebarToggle.focus();
    }
  });

  elements.primaryNavigation.addEventListener("click", async (event) => {
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (
      !link
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
    ) {
      return;
    }
    event.preventDefault();
    history.pushState({}, "", link.href);
    if (sidebarMedia.matches) {
      setSidebarCollapsed(true);
      elements.sidebarToggle.focus();
    }
    setFiltersFromUrl();
    setSignatureFiltersFromUrl();
    setDiagnosisFiltersFromUrl();
    setReportFiltersFromUrl();
    window.scrollTo(0, 0);
    await loadCurrentView();
  });

  window.addEventListener("popstate", async () => {
    setFiltersFromUrl();
    setSignatureFiltersFromUrl();
    setDiagnosisFiltersFromUrl();
    setReportFiltersFromUrl();
    if (storedSession().token) {
      await loadCurrentView();
    }
  });
}
