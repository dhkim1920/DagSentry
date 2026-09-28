import { elements } from "./elements.js";
import { resetConnectionForm } from "../components/connection-form.js";
import { state } from "./state.js";
import { setSidebarVisibility } from "./sidebar.js";
import { translatedText } from "./i18n.js";
import { clearSession } from "./session.js";
import { authHeaders, errorDetail } from "./api.js";

function setAuthLoading(isLoading) {
  elements.authLoading.hidden = !isLoading;
  elements.authSubmit.disabled = isLoading;
  elements.accessEmail.disabled = isLoading;
  elements.accessPassword.disabled = isLoading;
  elements.authPanel.setAttribute("aria-busy", String(isLoading));
}

export function showAuth(message = "") {
  setAuthLoading(false);
  setSidebarVisibility(false);
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
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
    const response = await fetch("/api/v1/auth/me");
    if (!response.ok) {
      clearSession();
      showAuth();
      return;
    }
    state.currentUser = await response.json();
    if (state.currentUser.must_change_password) {
      showPasswordChange();
      return;
    }
    await loadCurrentView();
  } catch {
    clearSession();
    showAuth("Unable to reach DagSentry.");
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
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        showAuth(await errorDetail(response));
        return;
      }
      const payload = await response.json();
      state.currentUser = payload.user;
      elements.accessPassword.value = "";
      if (state.currentUser.must_change_password) {
        showPasswordChange();
        return;
      }
      await loadCurrentView();
    } catch {
      showAuth("Unable to reach DagSentry.");
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
      const response = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (!response.ok) {
        elements.changePasswordError.textContent = await errorDetail(response);
        elements.changePasswordError.hidden = false;
        return;
      }
      clearSession();
      showAuth(translatedText("Password changed. Sign in again."));
    } catch {
      elements.changePasswordError.textContent = translatedText("Unable to reach DagSentry.");
      elements.changePasswordError.hidden = false;
    }
  });

  elements.disconnect.addEventListener("click", async () => {
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: authHeaders(),
    });
    if (!response.ok && response.status !== 401) {
      elements.sessionStatus.textContent = await errorDetail(response);
      return;
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
