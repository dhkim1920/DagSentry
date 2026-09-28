import { state } from "./state.js";

export function appendCell(row, content, className = "") {
  const cell = document.createElement("td");
  if (className) {
    cell.className = className;
  }
  if (content instanceof Node) {
    cell.append(content);
  } else {
    cell.textContent = content;
  }
  row.append(cell);
}

export function shortId(value) {
  return String(value).slice(0, 8).toUpperCase();
}

export function formatTimestamp(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return { primary: "Unavailable" };
  }
  const selectedTimezone = state.currentTimezone === "browser"
    ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    : state.currentTimezone;
  const primary = `${formatDateTime(timestamp, selectedTimezone)} ${timezoneLabel(timestamp, selectedTimezone)}`;
  return { primary };
}

export function formatDateTime(timestamp, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(timestamp);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

export function timezoneLabel(timestamp, timeZone) {
  if (timeZone === "UTC") {
    return "UTC";
  }
  if (timeZone === "Asia/Seoul") {
    return "KST";
  }
  const name = new Intl.DateTimeFormat("en", {
    timeZone,
    timeZoneName: "short",
  }).formatToParts(timestamp).find((part) => part.type === "timeZoneName");
  return name?.value || timeZone;
}

export function timestampBlock(value) {
  const formatted = formatTimestamp(value);
  const wrapper = document.createElement("div");
  const primary = document.createElement("div");
  primary.textContent = formatted.primary;
  wrapper.append(primary);
  return wrapper;
}

export function safeHttpUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function textElement(tagName, text, className = "") {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  element.textContent = text;
  return element;
}

export function definitionItem(term, description) {
  const wrapper = document.createElement("div");
  wrapper.append(textElement("dt", term), textElement("dd", description));
  return wrapper;
}

export function copyDefinitionItem(term, description) {
  const wrapper = document.createElement("div");
  wrapper.className = "copy-definition";
  const value = document.createElement("dd");
  value.append(textElement("code", description));
  const button = textElement("button", "Copy", "copy-button btn btn-outline-secondary btn-sm");
  button.type = "button";
  button.setAttribute("aria-label", `Copy ${term}`);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(description);
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1500);
    } catch {
      button.textContent = "Copy failed";
    }
  });
  value.append(button);
  wrapper.append(textElement("dt", term), value);
  return wrapper;
}

export function contextLink(label, href, external = false) {
  const link = document.createElement("a");
  link.className = "inspect-link btn btn-outline-secondary btn-sm";
  link.href = href;
  link.textContent = label;
  if (external) {
    link.target = "_blank";
    link.rel = "noreferrer noopener";
  }
  return link;
}
