import { beginViewRequest, currentViewRequest } from "../core/requests.js";
import { elements } from "../core/elements.js";
import { MANAGED_CONNECTION_PROVIDERS, configureConnectionForm, resetConnectionForm, editManagedConnection, managedConnectionRequestBody } from "../components/connection-form.js";
import { state } from "../core/state.js";
import { translatedText } from "../core/i18n.js";
import { apiJson, generateUuid } from "../core/api.js";
import { handleApiError } from "../core/auth.js";
import { adminPageFromUrl } from "../core/router.js";
import { appendCell, formatTimestamp, timestampBlock, textElement, definitionItem } from "../core/dom.js";
import { applyStatusColor } from "../components/badges.js";
import { loadReportSchedules } from "./reports.js";
import { loadOperations } from "./operations.js";

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

function renderConnectionRuntime(runtime) {
  elements.connectionRuntimeSummary.replaceChildren();
  elements.connectionRuntimeKeyWarning.hidden = true;
  elements.connectionRuntimeEnvironment.textContent = runtime?.environment || "—";
  if (!runtime) {
    elements.connectionRuntimeSummary.append(textElement("p", "Selection information unavailable. Refresh after upgrading the API."));
    return new Map();
  }
  elements.connectionRuntimeKeyWarning.hidden = runtime.encryption_key_configured
    || !runtime.selections.some(selection => selection.source === "database" && selection.secret_configured);
  const purposes = { AIRFLOW: "Airflow", LLM: "AI diagnosis", NOTIFICATION: "Notifications" };
  for (const selection of runtime.selections) {
    const column = textElement("div", "", "col-12 col-md-4");
    const panel = textElement("div", "", "card card-body h-100");
    panel.append(textElement("h4", purposes[selection.purpose], "card-title"));
    panel.append(textElement("span", selection.source === "database" ? "Database connections" : "Environment variables"));
    if (selection.source === "database" && !selection.connection_id) {
      panel.append(textElement("strong", "Enabled connection missing", "text-warning"));
    } else {
      const provider = textElement("code", selection.provider || "");
      provider.dataset.i18nFixed = "";
      panel.append(selection.provider ? provider : textElement("span", "Rule-based diagnosis only"));
      if (selection.version !== null) {
        panel.append(textElement("span", `${translatedText("Selected version")}: ${selection.version}`));
      }
      if (selection.fallback_providers.length) {
        panel.append(textElement("span", `${translatedText("Fallback providers")}: ${selection.fallback_providers.join(" → ")}`));
      }
    }
    column.append(panel);
    elements.connectionRuntimeSummary.append(column);
  }
  return new Map(runtime.selections.map(selection => [selection.purpose, selection]));
}

function renderAdminConnections(connections, runtime) {
  const selections = renderConnectionRuntime(runtime);
  if (runtime && !runtime.encryption_key_configured && connections.some(connection => (
    connection.enabled && connection.secret_configured && selections.get(connection.purpose)?.source === "database"
  ))) elements.connectionRuntimeKeyWarning.hidden = false;
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

    const selection = selections.get(connection.purpose);
    const selectionState = !connection.enabled ? "Disabled"
      : !selection ? "Selection unknown"
      : selection.source === "database" ? "Selected by API settings" : "Stored only";
    const selected = selection && connection.enabled && selection.source === "database";
    const selectionDetails = textElement("div", "", "task-identity");
    selectionDetails.append(textElement("span", selectionState, `badge ${selected ? "bg-azure-lt" : "bg-secondary-lt"}`));
    selectionDetails.append(textElement("span", `${translatedText("Saved version")}: ${connection.version}`));
    if (selection?.source === "environment" && connection.enabled) {
      const setting = textElement("code", `DAGSENTRY_${connection.purpose}_CONFIG_SOURCE=database`);
      setting.dataset.i18nFixed = "";
      selectionDetails.append(setting);
    }
    appendCell(row, selectionDetails);

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
    if (MANAGED_CONNECTION_PROVIDERS[connection.provider]?.testable && connection.enabled) {
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

const WORKER_JOB_PAGE_SIZE = 50;

function workerJobOffset() {
  const value = Number.parseInt(new URLSearchParams(window.location.search).get("offset") || "0", 10);
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function updateWorkerJobUrl(offset = 0) {
  const params = new URLSearchParams({ view: "admin", section: "jobs" });
  const environment = elements.workerJobEnvironment.value.trim();
  if (environment) params.set("environment", environment);
  if (offset) params.set("offset", String(offset));
  history.replaceState({}, "", `/ui/?${params}`);
}

async function requeueWorkerJob(job, button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/worker-jobs/${encodeURIComponent(job.outbox_id)}/requeue`,
      { method: "POST" },
    );
    if (!response) return;
    updateWorkerJobUrl();
    if (await loadAdminDashboard(false)) {
      showAdminFeedback(translatedText("Job queued for the Worker. Refresh to check for further failures."));
    }
  } catch (error) {
    showAdminError(translatedText(error instanceof Error ? error.message : "Unable to requeue job."));
  } finally {
    button.disabled = false;
  }
}

function renderWorkerJobs(payload) {
  elements.workerJobRows.replaceChildren();
  elements.workerJobEmpty.hidden = payload.items.length > 0;
  elements.workerJobTable.hidden = payload.items.length === 0;
  const reasons = {
    attempts_exhausted: "Attempts exhausted", permanent: "Permanent failure",
    retryable: "Retryable failure", stale_lock_reclaimed: "Stale lock reclaimed",
  };
  const stages = {
    diagnosis: "Diagnosis processing", load_event: "Load failure event",
    worker_recovery: "Worker recovery", airflow_connection: "Airflow connection",
    notification_connection: "Notification connection", llm_connection: "AI connection",
    notification: "Notification delivery",
  };
  for (const job of payload.items) {
    const row = document.createElement("tr");
    const identity = textElement("div", "", "task-identity");
    identity.dataset.i18nFixed = "";
    identity.append(textElement("strong", job.dag_id), textElement("span", job.task_id), textElement("span", job.environment));
    appendCell(row, identity);
    appendCell(row, String(job.attempt_count), "numeric");
    const reason = textElement("div", "", "task-identity");
    reason.append(
      textElement("strong", reasons[job.error_category] || "Error details unavailable"),
      textElement("span", stages[job.error_stage] || "Unknown processing stage"),
    );
    if (job.error_type) reason.append(textElement("code", job.error_type));
    appendCell(row, reason);
    appendCell(row, timestampBlock(job.updated_at));
    const details = document.createElement("details");
    details.append(textElement("summary", "Review and requeue"));
    const metadata = document.createElement("dl");
    metadata.className = "canonical-fields";
    metadata.append(
      definitionItem("DAG run", job.dag_run_id),
      definitionItem("Task try", String(job.try_number)),
      definitionItem("Map index", String(job.map_index)),
      definitionItem("Failure Event ID", job.failure_event_id),
      definitionItem("Job ID", job.outbox_id),
    );
    const button = adminActionButton("Requeue job", () => requeueWorkerJob(job, button), "btn btn-primary btn-sm");
    details.append(metadata, button);
    appendCell(row, details);
    ["Task", "Processing attempts", "Failure reason", "Last failure", "Review and requeue"].forEach((label, index) => {
      row.children[index].dataset.label = translatedText(label);
    });
    elements.workerJobRows.append(row);
  }
  const start = payload.items.length ? payload.offset + 1 : 0;
  elements.workerJobRange.textContent = `${start}–${payload.offset + payload.items.length} of ${payload.total}`;
  elements.workerJobPrevious.disabled = payload.offset === 0;
  elements.workerJobNext.disabled = payload.offset + payload.limit >= payload.total;
}

function showAdminPage(section) {
  const [title, description] = {
    users: ["User management", "Manage user accounts, roles, and sessions."],
    connections: ["External connections", "Manage Airflow, AI, and notification connections."],
    reports: ["Report settings", "Manage report delivery schedules and review recent runs."],
    jobs: ["Failed diagnosis jobs", "Inspect exhausted diagnosis jobs and requeue them after fixing the cause."],
    operations: ["Operational overview", "Check service observations and the diagnosis processing queue."],
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
    if (section === "operations") {
      const loaded = await loadOperations(request);
      if (request.isCurrent()) elements.adminContent.hidden = false;
      return loaded;
    }
    if (section === "reports") {
      const loaded = await loadReportSchedules(request);
      if (!request.isCurrent()) return false;
      elements.adminContent.hidden = false;
      return loaded;
    }
    let response;
    if (section === "connections") {
      response = await adminApiRequest("/api/v1/admin/connections?limit=200");
    } else if (section === "jobs") {
      const source = new URLSearchParams(window.location.search);
      elements.workerJobEnvironment.value = source.get("environment") || "";
      const query = new URLSearchParams({ limit: String(WORKER_JOB_PAGE_SIZE), offset: String(workerJobOffset()) });
      if (elements.workerJobEnvironment.value) query.set("environment", elements.workerJobEnvironment.value);
      response = await adminApiRequest(`/api/v1/admin/worker-jobs?${query}`);
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
      renderAdminConnections(payload.items, payload.runtime_configuration);
    } else if (section === "jobs") {
      renderWorkerJobs(payload);
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
  elements.operationsRefresh.addEventListener("click", () => loadAdminDashboard());
  elements.workerJobFilter.addEventListener("submit", async (event) => {
    event.preventDefault();
    updateWorkerJobUrl();
    await loadAdminDashboard();
  });
  elements.workerJobRefresh.addEventListener("click", () => loadAdminDashboard());
  for (const [button, direction] of [[elements.workerJobPrevious, -1], [elements.workerJobNext, 1]]) {
    button.addEventListener("click", async () => {
      updateWorkerJobUrl(Math.max(0, workerJobOffset() + direction * WORKER_JOB_PAGE_SIZE));
      await loadAdminDashboard();
    });
  }
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
