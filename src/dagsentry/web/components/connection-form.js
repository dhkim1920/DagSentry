import { elements } from "../core/elements.js";
import { state } from "../core/state.js";

export const MANAGED_CONNECTION_PROVIDERS = Object.freeze({
  AIRFLOW: { purpose: "AIRFLOW", apiBaseUrl: "http://localhost:8080/api/v2", secretField: "token", testable: true },
  OLLAMA: { purpose: "LLM", apiBaseUrl: "http://localhost:11434/api", secretField: null, testable: true },
  OPENAI: { purpose: "LLM", apiBaseUrl: "https://api.openai.com/v1", secretField: "api_key" },
  AZURE_OPENAI: { purpose: "LLM", apiBaseUrl: "", secretField: "api_key", urlField: "endpoint" },
  ANTHROPIC: { purpose: "LLM", apiBaseUrl: "https://api.anthropic.com/v1", secretField: "api_key" },
  BEDROCK: { purpose: "LLM", secretField: "access_key_id", extra: [
    { key: "region", label: "AWS region", required: true },
    { key: "secret_access_key", label: "AWS secret access key", secret: true },
    { key: "session_token", label: "AWS session token (optional)", secret: true },
  ] },
  SLACK: { purpose: "NOTIFICATION", apiBaseUrl: "https://slack.com/api", secretField: "bot_token", testable: true },
  WEBHOOK: { purpose: "NOTIFICATION", secretField: "url", extra: [
    { key: "bearer_token", label: "Bearer token (optional)", secret: true },
  ] },
  TEAMS: { purpose: "NOTIFICATION", secretField: "webhook_url" },
  DISCORD: { purpose: "NOTIFICATION", secretField: "webhook_url" },
  SMTP: { purpose: "NOTIFICATION", secretField: "password", extra: [
    { key: "host", label: "SMTP host", required: true },
    { key: "port", label: "SMTP port", type: "number", default: 587 },
    { key: "sender", label: "Sender email", type: "email", required: true },
    { key: "recipients", label: "Recipient emails (comma separated)", required: true, list: true },
    { key: "use_starttls", label: "Use STARTTLS", type: "checkbox", default: true },
    { key: "use_ssl", label: "Use implicit TLS", type: "checkbox", default: false },
    { key: "username", label: "SMTP username", secret: true },
  ] },
});

export function configureConnectionForm(useDefaultApiUrl = false) {
  const providerName = elements.connectionProvider.value;
  const provider = MANAGED_CONNECTION_PROVIDERS[providerName];
  const isAirflow = providerName === "AIRFLOW";
  const isLLM = provider.purpose === "LLM";
  elements.connectionApiUrlGroup.hidden = provider.apiBaseUrl === undefined;
  elements.connectionApiBaseUrl.required = provider.apiBaseUrl !== undefined;
  elements.connectionUiUrlGroup.hidden = !isAirflow;
  elements.connectionModelGroup.hidden = !isLLM;
  elements.connectionChannelGroup.hidden = providerName !== "SLACK";
  elements.connectionSecretGroup.hidden = provider.secretField === null;
  elements.connectionModel.required = isLLM;
  elements.connectionChannel.required = providerName === "SLACK";
  if (useDefaultApiUrl) {
    elements.connectionApiBaseUrl.value = provider.apiBaseUrl || "";
    elements.connectionSecret.value = "";
  }
  elements.connectionSecretLabel.textContent = {
    token: "Token", bot_token: "Bot token", api_key: "API key",
    access_key_id: "AWS access key ID", password: "SMTP password",
    url: "Webhook URL", webhook_url: "Webhook URL",
  }[provider.secretField] || "Secret";
  elements.connectionExtraFields.replaceChildren();
  for (const field of provider.extra || []) {
    const group = document.createElement("div");
    group.className = "field-group";
    const input = document.createElement("input");
    input.id = `connection-extra-${field.key}`;
    input.name = field.key;
    input.type = field.secret ? "password" : field.type || "text";
    input.className = input.type === "checkbox" ? "form-check-input" : "form-control";
    input.required = Boolean(field.required);
    if (field.secret) input.autocomplete = "new-password";
    if (field.type === "number") { input.min = "1"; input.max = "65535"; }
    const stored = state.editingConnection?.non_secret_config[field.key];
    const value = field.secret ? "" : stored ?? field.default ?? "";
    if (field.type === "checkbox") input.checked = Boolean(value);
    else input.value = field.list && Array.isArray(value) ? value.join(", ") : String(value);
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.className = "form-label";
    label.textContent = field.label;
    group.append(label, input);
    elements.connectionExtraFields.append(group);
  }
}

export function resetConnectionForm() {
  state.editingConnection = null;
  elements.connectionForm.reset();
  elements.connectionProvider.disabled = false;
  elements.connectionEnvironment.disabled = false;
  elements.connectionCancelEdit.hidden = true;
  elements.connectionSave.textContent = "Create connection";
  elements.connectionSecretHelp.textContent = "Write-only. Leave blank while editing to preserve the configured Secret.";
  configureConnectionForm(true);
}

export function editManagedConnection(connection) {
  if (!MANAGED_CONNECTION_PROVIDERS[connection.provider]) {
    return;
  }
  state.editingConnection = connection;
  elements.connectionEnvironment.value = connection.environment;
  elements.connectionProvider.value = connection.provider;
  elements.connectionDisplayName.value = connection.display_name;
  elements.connectionApiBaseUrl.value = connection.non_secret_config.api_base_url || connection.non_secret_config.endpoint || "";
  elements.connectionUiUrl.value = connection.non_secret_config.ui_base_url || "";
  elements.connectionModel.value = connection.non_secret_config.model || connection.non_secret_config.model_id || "";
  elements.connectionChannel.value = connection.non_secret_config.channel || "";
  elements.connectionSecret.value = "";
  elements.connectionProvider.disabled = true;
  elements.connectionEnvironment.disabled = true;
  elements.connectionCancelEdit.hidden = false;
  elements.connectionSave.textContent = "Save connection";
  elements.connectionSecretHelp.textContent = connection.secret_configured
    ? "Secret configured. Leave blank to preserve it, or enter a replacement."
    : "No Secret configured. Enter one to configure it.";
  configureConnectionForm();
  elements.connectionDisplayName.focus();
}

export function managedConnectionRequestBody() {
  const providerName = state.editingConnection?.provider || elements.connectionProvider.value;
  const provider = MANAGED_CONNECTION_PROVIDERS[providerName];
  const nonSecretConfig = state.editingConnection
    ? { ...state.editingConnection.non_secret_config }
    : {};
  if (provider.apiBaseUrl !== undefined) {
    nonSecretConfig[provider.urlField || "api_base_url"] = elements.connectionApiBaseUrl.value.trim();
  }
  if (providerName === "AIRFLOW") {
    delete nonSecretConfig.ui_base_url;
    if (elements.connectionUiUrl.value.trim()) nonSecretConfig.ui_base_url = elements.connectionUiUrl.value.trim();
  }
  if (provider.purpose === "LLM") {
    nonSecretConfig[providerName === "BEDROCK" ? "model_id" : "model"] = elements.connectionModel.value.trim();
  }
  if (providerName === "SLACK") nonSecretConfig.channel = elements.connectionChannel.value.trim();
  const secret = {};
  if (provider.secretField && elements.connectionSecret.value) {
    secret[provider.secretField] = elements.connectionSecret.value;
  }
  for (const field of provider.extra || []) {
    const input = elements.connectionExtraFields.querySelector(`[name="${field.key}"]`);
    if (field.secret) {
      if (input.value) secret[field.key] = input.value;
    } else {
      nonSecretConfig[field.key] = field.type === "checkbox" ? input.checked
        : field.type === "number" ? Number(input.value)
        : field.list ? input.value.split(",").map(value => value.trim()).filter(Boolean)
        : input.value.trim();
    }
  }
  const body = {
    environment: state.editingConnection?.environment || elements.connectionEnvironment.value.trim(),
    purpose: provider.purpose,
    provider: providerName,
    display_name: elements.connectionDisplayName.value.trim(),
    non_secret_config: nonSecretConfig,
    enabled: true,
  };
  if (state.editingConnection) {
    body.expected_version = state.editingConnection.version;
  }
  if (Object.keys(secret).length) body.secret = secret;
  return body;
}
