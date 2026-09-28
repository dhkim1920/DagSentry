import { currentViewRequest } from "../../core/requests.js";
import { formatTimestamp, textElement } from "../../core/dom.js";
import { elements } from "../../core/elements.js";
import { state } from "../../core/state.js";
import { translatedText } from "../../core/i18n.js";
import { storedSession } from "../../core/session.js";
import { apiRequest, ApiError } from "../../core/api.js";
import { handleApiError } from "../../core/auth.js";

let refreshIncident;

const OPERATOR_ACTIONS = {
  OPEN: [
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  ACKNOWLEDGED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  RECOVERED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Confirm the recovered Incident requires no further operational work.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this recovered Incident without further operational work.",
    },
  ],
  RESOLVED: [],
  IGNORED: [],
};

const TERMINAL_OPERATOR_ACTIONS = {
  RESOLVED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  IGNORED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
  ],
};

function transitionStatusLabel(transition) {
  if (
    ["RESOLVED", "IGNORED"].includes(transition.previous_status)
    && transition.status === "OPEN"
  ) {
    return "REOPENED";
  }
  return transition.status;
}

export function renderTransitions(transitions) {
  elements.transitionHistory.replaceChildren();
  elements.transitionCount.textContent = String(transitions.length);
  elements.transitionHistory.classList.toggle("is-scrollable", transitions.length > 3);
  elements.transitionEmpty.hidden = transitions.length !== 0;
  const orderedTransitions = [...transitions].reverse();
  for (const transition of orderedTransitions) {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "transition-heading";
    const statusLabel = transitionStatusLabel(transition);
    heading.append(
      textElement("strong", translatedText(statusLabel)),
      textElement("time", formatTimestamp(transition.created_at).primary),
    );
    const detail = textElement(
      "p",
      `${translatedText(transition.previous_status)} → ${translatedText(statusLabel)} · ${transition.actor}`,
    );
    item.append(heading, detail);
    if (transition.reason) {
      item.append(textElement("blockquote", transition.reason));
    }
    elements.transitionHistory.append(item);
  }
}

function closeTransitionDialog() {
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
  }
  state.pendingOperatorAction = null;
  elements.transitionError.hidden = true;
}

function openTransitionDialog(action) {
  state.pendingOperatorAction = action;
  elements.transitionDialogTitle.textContent = action.label;
  elements.transitionDialogCopy.textContent = action.copy;
  elements.transitionReason.value = "";
  elements.transitionError.hidden = true;
  elements.transitionConfirm.textContent = action.label;
  elements.transitionDialog.showModal();
  elements.transitionReason.focus();
}

export function renderOperatorControls(status, transitions) {
  const isOperator = ["operator", "admin"].includes(storedSession().role);
  elements.operatorControls.hidden = !isOperator;
  elements.viewerStateNote.hidden = isOperator;
  elements.operatorActions.replaceChildren();
  if (!isOperator) {
    return;
  }

  const terminalActions = TERMINAL_OPERATOR_ACTIONS[status];
  const lastTransition = transitions[transitions.length - 1];
  const canOverrideTerminal = storedSession().role === "admin"
    || (lastTransition && state.currentUser && lastTransition.actor === state.currentUser.email);
  const actions = terminalActions
    ? canOverrideTerminal ? terminalActions : []
    : OPERATOR_ACTIONS[status] || [];
  elements.operatorHelp.textContent = terminalActions
    ? canOverrideTerminal
      ? "This terminal Incident can be changed by its last operator or an Admin."
      : "Only the operator who made the terminal change or an Admin can change this Incident."
    : "Choose an explicit state change. Every change is added to the audit trail.";
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action.status === "IGNORED"
      ? "btn btn-outline-danger"
      : action.status === "RESOLVED"
        ? "btn btn-primary"
        : "btn btn-outline-secondary";
    button.textContent = action.label;
    button.addEventListener("click", () => openTransitionDialog(action));
    elements.operatorActions.append(button);
  }
}

function setTransitionSubmitting(isSubmitting) {
  elements.transitionForm.setAttribute("aria-busy", String(isSubmitting));
  elements.transitionClose.disabled = isSubmitting;
  elements.transitionCancel.disabled = isSubmitting;
  elements.transitionConfirm.disabled = isSubmitting;
}

async function submitOperatorTransition() {
  const request = currentViewRequest();
  if (!state.pendingOperatorAction || !state.currentIncidentId || !state.currentIncidentStatus) {
    return;
  }

  const action = state.pendingOperatorAction;
  const incidentId = state.currentIncidentId;
  const body = {
    status: action.status,
    expected_status: state.currentIncidentStatus,
  };
  const reason = elements.transitionReason.value.trim();
  if (reason) {
    body.reason = reason;
  }

  setTransitionSubmitting(true);
  elements.transitionError.hidden = true;
  try {
    await apiRequest(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/status`,
      {
        signal: request.signal,
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!request.isCurrent()) return;
    closeTransitionDialog();
    await refreshIncident(incidentId, `Incident changed to ${action.status}.`);
  } catch (error) {
    if (!request.isCurrent() || handleApiError(error)) return;
    if (error instanceof ApiError && error.status === 409) {
      closeTransitionDialog();
      await refreshIncident(incidentId, `${error.message}. The Incident was refreshed; reconsider the current state.`);
      return;
    }
    elements.transitionError.textContent = error instanceof Error
      ? error.message
      : "Unable to change the Incident state";
    elements.transitionError.hidden = false;
  } finally {
    setTransitionSubmitting(false);
  }
}

export function bindTransitionsEvents(onChanged) {
  refreshIncident = onChanged;
  elements.transitionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitOperatorTransition();
  });

  elements.transitionClose.addEventListener("click", closeTransitionDialog);

  elements.transitionCancel.addEventListener("click", closeTransitionDialog);

  elements.transitionDialog.addEventListener("cancel", (event) => {
    if (elements.transitionConfirm.disabled) {
      event.preventDefault();
      return;
    }
    state.pendingOperatorAction = null;
    elements.transitionError.hidden = true;
  });
}
