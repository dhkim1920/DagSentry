import { beginViewRequest, currentViewRequest } from "../core/requests.js";
import { elements } from "../core/elements.js";
import { MANAGED_CONNECTION_PROVIDERS, configureConnectionForm, resetConnectionForm, editManagedConnection, managedConnectionRequestBody } from "../components/connection-form.js";
import { state } from "../core/state.js";
import { translatedText } from "../core/i18n.js";
import { apiJson, generateUuid } from "../core/api.js";
import { handleApiError } from "../core/auth.js";
import { adminPageFromUrl } from "../core/router.js";
import { appendCell, formatTimestamp, timestampBlock, textElement } from "../core/dom.js";
import { applyStatusColor } from "../components/badges.js";
import { loadReportSchedules } from "./reports.js";

function showAdminError(message) {
  elements.adminError.textContent = message;
  elements.adminError.hidden = false;
  elements.adminFeedback.hidden = true;
}

function showAdminFeedback(message) {
  elements.adminFeedback.textContent = message;
  elements.adminFeedback.hidden = false;
  elements.adminError.hidden = true;
  elements.adminFeedback.focus();
}

async function adminApiRequest(url, options = {}) {
  const request = currentViewRequest();
  try {
    return await apiJson(url, { ...options, signal: request.signal });
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return null;
    throw error;
  }
}

function adminActionButton(label, action, className = "btn btn-outline-secondary") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function updateManagedUser(userId, change, successMessage) {
  try {
    const response = await adminApiRequest(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
    if (response) {
      showAdminFeedback(successMessage);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to update user");
  }
}

async function revokeManagedUserSessions(user) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/users/${encodeURIComponent(user.id)}/revoke-sessions`,
      { method: "POST" },
    );
    if (response) {
      const result = response;
      showAdminFeedback(`Revoked ${result.sessions_revoked} session(s) for ${user.email}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to revoke sessions");
  }
}

function openPasswordResetDialog(user) {
  state.passwordResetUser = user;
  elements.passwordResetCopy.textContent = `Set a temporary password for ${user.email}. All existing sessions will be revoked.`;
  elements.passwordResetValue.value = "";
  elements.passwordResetError.hidden = true;
  elements.passwordResetDialog.showModal();
  elements.passwordResetValue.focus();
}

function closePasswordResetDialog() {
  state.passwordResetUser = null;
  elements.passwordResetValue.value = "";
  elements.passwordResetDialog.close();
}

function renderAdminUsers(users) {
  elements.adminUserRows.replaceChildren();
  for (const user of users) {
    const row = document.createElement("tr");
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", user.display_name),
      textElement("span", user.email),
      ...(user.must_change_password
        ? [textElement("span", "Password change required", "muted-label")]
        : []),
    );
    appendCell(row, identity);

    const statusBadge = textElement("span", user.status, "status-badge badge");
    statusBadge.dataset.status = user.status;
    applyStatusColor(statusBadge, user.status);
    appendCell(row, statusBadge);

    const roleSelect = document.createElement("select");
    roleSelect.className = "admin-role-select form-select";
    roleSelect.setAttribute("aria-label", `Role for ${user.email}`);
    for (const role of ["VIEWER", "OPERATOR", "ADMIN"]) {
      const option = document.createElement("option");
      option.value = role;
      option.textContent = role;
      option.selected = role === user.role;
      roleSelect.append(option);
    }
    appendCell(row, roleSelect);
    appendCell(row, user.last_login_at ? timestampBlock(user.last_login_at) : "Never");

    const actions = document.createElement("div");
    actions.className = "admin-actions";
    actions.append(
      adminActionButton("Save role", () => updateManagedUser(
        user.id,
        { role: roleSelect.value },
        `Updated role for ${user.email}.`,
      )),
      adminActionButton(
        user.status === "ACTIVE" ? "Disable" : "Enable",
        () => updateManagedUser(
          user.id,
          { status: user.status === "ACTIVE" ? "DISABLED" : "ACTIVE" },
          `${user.status === "ACTIVE" ? "Disabled" : "Enabled"} ${user.email}.`,
        ),
        user.status === "ACTIVE" ? "btn btn-outline-danger" : "btn btn-outline-secondary",
      ),
      adminActionButton("Reset password", () => openPasswordResetDialog(user)),
      adminActionButton("Revoke sessions", () => revokeManagedUserSessions(user)),
    );
    appendCell(row, actions);
    elements.adminUserRows.append(row);
  }
}

async function testManagedConnection(connection) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/connections/${encodeURIComponent(connection.id)}/test`,
      { method: "POST" },
    );
    if (response) {
      const result = response;
      const detail = result.error_category ? ` · ${result.error_category}` : "";
      showAdminFeedback(`Connection test ${result.status}${detail}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to test connection");
  }
}

async function disableManagedConnection(connection) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/connections/${encodeURIComponent(connection.id)}/disable`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: connection.version }),
      },
    );
    if (response) {
      if (state.editingConnection?.id === connection.id) {
        resetConnectionForm();
      }
      showAdminFeedback(`Disabled ${connection.display_name}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to disable connection");
  }
}

function renderAdminConnections(connections) {
  elements.adminConnectionRows.replaceChildren();
  const hasConnections = connections.length > 0;
  elements.adminConnectionsEmpty.hidden = hasConnections;
  elements.adminConnectionsTable.hidden = !hasConnections;
  for (const connection of connections) {
    const row = document.createElement("tr");
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", connection.display_name),
      textElement("span", `${connection.environment} · ${connection.enabled ? "ENABLED" : "DISABLED"}`),
    );
    appendCell(row, identity);
    appendCell(row, `${connection.purpose} · ${connection.provider}`);

    const secret = document.createElement("div");
    secret.className = "connection-secret-state";
    secret.append(textElement(
      "span",
      connection.secret_configured ? "•••••• Configured" : "Not configured",
    ));
    appendCell(row, secret);

    const lastTest = document.createElement("div");
    lastTest.className = "task-identity";
    if (connection.last_test_status) {
      const badge = textElement("span", connection.last_test_status, "status-badge badge");
      badge.dataset.status = connection.last_test_status;
      applyStatusColor(badge, connection.last_test_status);
      lastTest.append(
        badge,
        textElement("span", connection.last_test_error_category || "No error"),
        textElement("span", formatTimestamp(connection.last_tested_at).primary),
      );
    } else {
      lastTest.append(textElement("span", "Never tested"));
    }
    appendCell(row, lastTest);

    const actions = document.createElement("div");
    actions.className = "admin-actions";
    const supported = Boolean(MANAGED_CONNECTION_PROVIDERS[connection.provider]);
    if (supported) {
      actions.append(adminActionButton("Edit", () => editManagedConnection(connection)));
    }
    if (supported && connection.enabled) {
      actions.append(adminActionButton("Test", () => testManagedConnection(connection)));
    }
    if (connection.enabled) {
      actions.append(adminActionButton(
        "Disable",
        () => disableManagedConnection(connection),
        "btn btn-outline-danger",
      ));
    }
    appendCell(row, actions);
    elements.adminConnectionRows.append(row);
  }
}

function renderAdminAudit(events) {
  elements.adminAuditRows.replaceChildren();
  for (const event of events) {
    const row = document.createElement("tr");
    appendCell(row, timestampBlock(event.created_at));
    appendCell(row, event.actor_email || "System / recovery CLI");
    appendCell(row, event.action);
    appendCell(row, `${event.target_type} · ${event.target_id.slice(0, 8)}…`);
    appendCell(row, textElement("code", JSON.stringify(event.change_summary), "admin-summary"));
    elements.adminAuditRows.append(row);
  }
}

function showAdminPage(section) {
  const [title, description] = {
    users: ["User management", "Manage user accounts, roles, and sessions."],
    connections: ["External connections", "Configure and test Airflow, Ollama, and Slack connections."],
    reports: ["Report settings", "Manage report delivery schedules and review recent runs."],
    audit: ["Administrator audit history", "Review administrator changes to users and external connections."],
  }[section];
  elements.adminDashboardTitle.textContent = title;
  elements.adminDashboardDescription.textContent = description;
  document.title = `${translatedText("DagSentry — Settings")} · ${translatedText(title)}`;
  for (const page of elements.adminPages) {
    page.hidden = page.dataset.settingsPage !== section;
  }
  elements.adminUserTotal.closest(".metric-card").hidden = section !== "users";
  elements.adminConnectionTotal.closest(".metric-card").hidden = section !== "connections";
}

export async function loadAdminDashboard(showLoading = true) {
  const section = adminPageFromUrl();
  const request = beginViewRequest();
  if (showLoading) {
    showAdminPage(section);
    elements.adminFeedback.hidden = true;
    elements.adminLoading.hidden = false;
    elements.adminContent.hidden = true;
  }
  elements.adminError.hidden = true;
  try {
    if (section === "reports") {
      const loaded = await loadReportSchedules(request);
      if (!request.isCurrent()) return false;
      elements.adminContent.hidden = false;
      return loaded;
    }
    let response;
    if (section === "connections") {
      response = await adminApiRequest("/api/v1/admin/connections?limit=200");
    } else if (section === "audit") {
      response = await adminApiRequest("/api/v1/admin/audit-events?limit=100");
    } else {
      response = await adminApiRequest("/api/v1/admin/users?limit=200");
    }
    if (!response) return false;
    const payload = response;
    if (!request.isCurrent()) return false;
    if (section === "connections") {
      elements.adminConnectionTotal.textContent = String(payload.total);
      renderAdminConnections(payload.items);
    } else if (section === "audit") {
      renderAdminAudit(payload.items);
    } else {
      elements.adminUserTotal.textContent = String(payload.total);
      renderAdminUsers(payload.items);
    }
    elements.adminContent.hidden = false;
    return true;
  } catch (error) {
    if (request.isCurrent()) {
      showAdminError(error instanceof Error ? error.message : "Unable to load settings");
    }
    return false;
  } finally {
    if (request.isCurrent()) elements.adminLoading.hidden = true;
  }
}

export function bindAdminEvents() {
  elements.adminCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(elements.adminCreateForm);
    const body = Object.fromEntries(data.entries());
    try {
      const response = await adminApiRequest("/api/v1/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (response) {
        const user = response;
        elements.adminCreateForm.reset();
        showAdminFeedback(`Created ${user.email}.`);
        await loadAdminDashboard(false);
      }
    } catch (error) {
      showAdminError(error instanceof Error ? error.message : "Unable to create user");
    }
  });

  elements.connectionProvider.addEventListener("change", () => configureConnectionForm(true));

  elements.connectionCancelEdit.addEventListener("click", resetConnectionForm);

  elements.connectionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const connectionId = state.editingConnection?.id || generateUuid();
      const response = await adminApiRequest(
        `/api/v1/admin/connections/${encodeURIComponent(connectionId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(managedConnectionRequestBody()),
        },
      );
      if (response) {
        const saved = response;
        resetConnectionForm();
        showAdminFeedback(`Saved ${saved.display_name}.`);
        await loadAdminDashboard(false);
      }
    } catch (error) {
      showAdminError(error instanceof Error ? error.message : "Unable to save connection");
    }
  });

  elements.passwordResetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.passwordResetUser) {
      return;
    }
    const user = state.passwordResetUser;
    try {
      const response = await adminApiRequest(
        `/api/v1/admin/users/${encodeURIComponent(user.id)}/reset-password`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ temporary_password: elements.passwordResetValue.value }),
        },
      );
      if (response) {
        closePasswordResetDialog();
        showAdminFeedback(`Reset the temporary password for ${user.email}.`);
        await loadAdminDashboard(false);
      }
    } catch (error) {
      elements.passwordResetError.textContent = error instanceof Error
        ? error.message
        : "Unable to reset password";
      elements.passwordResetError.hidden = false;
    }
  });

  elements.passwordResetClose.addEventListener("click", closePasswordResetDialog);

  elements.passwordResetCancel.addEventListener("click", closePasswordResetDialog);

  elements.passwordResetDialog.addEventListener("cancel", () => {
    state.passwordResetUser = null;
    elements.passwordResetValue.value = "";
  });
}
