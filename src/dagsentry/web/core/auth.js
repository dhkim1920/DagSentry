import { cancelViewRequests } from "./requests.js";
import { elements } from "./elements.js";
import { resetConnectionForm } from "../components/connection-form.js";
import { state } from "./state.js";
import { setSidebarVisibility } from "./sidebar.js";
import { translatedText } from "./i18n.js";
import { clearSession } from "./session.js";
import { apiRequest, apiJson, ApiError } from "./api.js";

function setAuthLoading(isLoading) {
  elements.authLoading.hidden = !isLoading;
  elements.authSubmit.disabled = isLoading;
  elements.accessEmail.disabled = isLoading;
  elements.accessPassword.disabled = isLoading;
  elements.authPanel.setAttribute("aria-busy", String(isLoading));
}

export function showAuth(message = "") {
  cancelViewRequests();
  setAuthLoading(false);
  setSidebarVisibility(false);
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
  }
  if (elements.humanDiagnosisDialog.open) {
    elements.humanDiagnosisDialog.close();
  }
  if (elements.passwordResetDialog.open) {
    elements.passwordResetDialog.close();
  }
  state.pendingOperatorAction = null;
  state.passwordResetUser = null;
  resetConnectionForm();
  elements.dashboard.hidden = true;
  elements.detail.hidden = true;
  elements.signatureDashboard.hidden = true;
  elements.signatureDetail.hidden = true;
  elements.diagnosisDashboard.hidden = true;
  elements.diagnosisDetail.hidden = true;
  elements.reportDashboard.hidden = true;
  elements.reportDetail.hidden = true;
  elements.adminDashboard.hidden = true;
  elements.adminNavigation.hidden = true;
  elements.authPanel.hidden = false;
  elements.authForm.hidden = false;
  elements.changePasswordForm.hidden = true;
  elements.currentPassword.value = "";
  elements.newPassword.value = "";
  elements.confirmNewPassword.value = "";
  elements.changePasswordError.hidden = true;
  document.title = translatedText("DagSentry — Connect");
  elements.disconnect.hidden = true;
  elements.sessionStatus.textContent = "Not connected";
  elements.authError.textContent = message;
  elements.authError.hidden = !message;
  if (message) {
    elements.accessEmail.focus();
  }
}

export function showPasswordChange() {
  cancelViewRequests();
  if (!state.currentUser) {
    showAuth();
    return;
  }
  setSidebarVisibility(false);
  elements.dashboard.hidden = true;
  elements.detail.hidden = true;
  elements.signatureDashboard.hidden = true;
  elements.signatureDetail.hidden = true;
  elements.diagnosisDashboard.hidden = true;
  elements.diagnosisDetail.hidden = true;
  elements.reportDashboard.hidden = true;
  elements.reportDetail.hidden = true;
  elements.adminDashboard.hidden = true;
  elements.adminNavigation.hidden = true;
  elements.authPanel.hidden = false;
  elements.authForm.hidden = true;
  elements.changePasswordForm.hidden = false;
  elements.changePasswordError.hidden = true;
  elements.disconnect.hidden = false;
  elements.sessionStatus.textContent = `${state.currentUser.display_name} · ${translatedText("Password change required")}`;
  document.title = translatedText("DagSentry — Change temporary password");
  elements.currentPassword.focus();
}

export async function restoreSession(loadCurrentView) {
  try {
    state.currentUser = await apiJson("/api/v1/auth/me");
    if (state.currentUser.must_change_password) {
      showPasswordChange();
      return;
    }
    await loadCurrentView();
  } catch (error) {
    clearSession();
    showAuth(error instanceof ApiError && error.status === 401 ? "" : "Unable to reach DagSentry.");
  } finally {
    elements.sessionLoading.hidden = true;
  }
}

export function bindAuthEvents(loadCurrentView) {
  elements.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = elements.accessEmail.value.trim();
    const password = elements.accessPassword.value;
    if (!email || !password) {
      showAuth("Enter your email and password.");
      return;
    }
    elements.authError.hidden = true;
    setAuthLoading(true);
    try {
      const payload = await apiJson("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      state.currentUser = payload.user;
      elements.accessPassword.value = "";
      if (state.currentUser.must_change_password) {
        showPasswordChange();
        return;
      }
      await loadCurrentView();
    } catch (error) {
      showAuth(error instanceof ApiError ? error.message : "Unable to reach DagSentry.");
    } finally {
      setAuthLoading(false);
    }
  });

  elements.changePasswordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const currentPassword = elements.currentPassword.value;
    const newPassword = elements.newPassword.value;
    if (newPassword !== elements.confirmNewPassword.value) {
      elements.changePasswordError.textContent = translatedText("Passwords do not match.");
      elements.changePasswordError.hidden = false;
      return;
    }
    elements.changePasswordError.hidden = true;
    try {
      await apiRequest("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      clearSession();
      showAuth(translatedText("Password changed. Sign in again."));
    } catch (error) {
      elements.changePasswordError.textContent = error instanceof ApiError
        ? error.message : translatedText("Unable to reach DagSentry.");
      elements.changePasswordError.hidden = false;
    }
  });

  elements.disconnect.addEventListener("click", async () => {
    cancelViewRequests();
    try {
      await apiRequest("/api/v1/auth/logout", { method: "POST" });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        elements.sessionStatus.textContent = error instanceof Error ? error.message : "Unable to sign out.";
        return;
      }
    }
    clearSession();
    elements.rows.replaceChildren();
    elements.signatureRows.replaceChildren();
    elements.signatureOccurrenceRows.replaceChildren();
    elements.diagnosisRows.replaceChildren();
    elements.reportRows.replaceChildren();
    elements.adminUserRows.replaceChildren();
    elements.adminConnectionRows.replaceChildren();
    elements.adminAuditRows.replaceChildren();
    resetConnectionForm();
    showAuth();
  });
}

// Transport errors stay in the current view; only 401 invalidates the session.
export function handleApiError(error) {
  if (error?.name === "AbortError") return true;
  if (error instanceof ApiError && error.status === 401) {
    clearSession();
    showAuth(error.message);
    return true;
  }
  return false;
}
