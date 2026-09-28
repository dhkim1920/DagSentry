import { elements } from "./core/elements.js";
import { resetConnectionForm } from "./components/connection-form.js";
import { bindListEvents } from "./views/incidents/list.js";
import { bindIncidentDetailEvents } from "./views/incidents/detail.js";
import { LANGUAGE_STORAGE_KEY, TIMEZONE_STORAGE_KEY } from "./core/constants.js";
import { state } from "./core/state.js";
import { applySidebarState, setSidebarVisibility } from "./core/sidebar.js";
import { applyLanguage, bindI18NEvents } from "./core/i18n.js";
import { storedSession } from "./core/session.js";
import { showAuth, restoreSession, bindAuthEvents } from "./core/auth.js";
import { setFiltersFromUrl, setSignatureFiltersFromUrl, setDiagnosisFiltersFromUrl, setReportFiltersFromUrl } from "./core/router.js";
import { bindSignaturesEvents } from "./views/signatures.js";
import { bindDiagnosesEvents } from "./views/diagnoses.js";
import { bindReportsEvents } from "./views/reports.js";
import { bindAdminEvents } from "./views/admin.js";
import { loadCurrentView, bindNavigationEvents } from "./core/navigation.js";

function bindAppEvents() {
  elements.languageSelect.addEventListener("change", async () => {
    state.currentLanguage = elements.languageSelect.value;
    localStorage.setItem(LANGUAGE_STORAGE_KEY, state.currentLanguage);
    applyLanguage();
    applySidebarState();
    if (storedSession().token) {
      await loadCurrentView();
    } else {
      showAuth();
    }
    applyLanguage();
    applySidebarState();
  });

  elements.timezoneSelect.addEventListener("change", async () => {
    state.currentTimezone = elements.timezoneSelect.value;
    localStorage.setItem(TIMEZONE_STORAGE_KEY, state.currentTimezone);
    if (storedSession().token) {
      await loadCurrentView();
    }
  });
}

bindListEvents();
bindIncidentDetailEvents();
bindAdminEvents();
bindAuthEvents(loadCurrentView);
bindNavigationEvents();
bindSignaturesEvents();
bindDiagnosesEvents();
bindReportsEvents();
bindI18NEvents();
bindAppEvents();

applyLanguage();
setSidebarVisibility(false);
resetConnectionForm();
setFiltersFromUrl();
setSignatureFiltersFromUrl();
setDiagnosisFiltersFromUrl();
setReportFiltersFromUrl();
restoreSession(loadCurrentView);
