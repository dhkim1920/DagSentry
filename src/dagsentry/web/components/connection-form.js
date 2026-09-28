import { elements } from "../core/elements.js";
import { state } from "../core/state.js";

export const MANAGED_CONNECTION_PROVIDERS = Object.freeze({
  AIRFLOW: {
    purpose: "AIRFLOW",
    apiBaseUrl: "http://localhost:8080/api/v2",
    secretField: "token",
  },
  OLLAMA: {
    purpose: "LLM",
    apiBaseUrl: "http://localhost:11434/api",
    secretField: null,
  },
  SLACK: {
    purpose: "NOTIFICATION",
    apiBaseUrl: "https://slack.com/api",
    secretField: "bot_token",
  },
});

export function configureConnectionForm(useDefaultApiUrl = false) {
  const providerName = elements.connectionProvider.value;
  const provider = MANAGED_CONNECTION_PROVIDERS[providerName];
  const isAirflow = providerName === "AIRFLOW";
  const isOllama = providerName === "OLLAMA";
  const isSlack = providerName === "SLACK";

  elements.connectionUiUrlGroup.hidden = !isAirflow;
  elements.connectionModelGroup.hidden = !isOllama;
  elements.connectionChannelGroup.hidden = !isSlack;
  elements.connectionSecretGroup.hidden = provider.secretField === null;
  elements.connectionModel.required = isOllama;
  elements.connectionChannel.required = isSlack;
  if (useDefaultApiUrl) {
    elements.connectionApiBaseUrl.value = provider.apiBaseUrl;
  }
  elements.connectionSecretLabel.textContent = isSlack ? "Bot token" : "Token";
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
  elements.connectionApiBaseUrl.value = connection.non_secret_config.api_base_url || "";
  elements.connectionUiUrl.value = connection.non_secret_config.ui_base_url || "";
  elements.connectionModel.value = connection.non_secret_config.model || "";
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
  nonSecretConfig.api_base_url = elements.connectionApiBaseUrl.value.trim();
  delete nonSecretConfig.ui_base_url;
  delete nonSecretConfig.model;
  delete nonSecretConfig.channel;
  if (providerName === "AIRFLOW" && elements.connectionUiUrl.value.trim()) {
    nonSecretConfig.ui_base_url = elements.connectionUiUrl.value.trim();
  } else if (providerName === "OLLAMA") {
    nonSecretConfig.model = elements.connectionModel.value.trim();
  } else if (providerName === "SLACK") {
    nonSecretConfig.channel = elements.connectionChannel.value.trim();
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
  if (provider.secretField && elements.connectionSecret.value) {
    body.secret = { [provider.secretField]: elements.connectionSecret.value };
  }
  return body;
}
