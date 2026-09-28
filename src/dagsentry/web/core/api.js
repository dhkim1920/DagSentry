export function authHeaders() {
  const csrfToken = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("dagsentry_csrf="));
  if (!csrfToken) {
    return {};
  }
  return { "X-CSRF-Token": decodeURIComponent(csrfToken.split("=").slice(1).join("=")) };
}

function normalizedErrorDetail(detail, fallback) {
  if (typeof detail === "string" && detail) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (!item || typeof item.msg !== "string") {
        return [];
      }
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== "body").join(".")
        : "";
      return location ? `${location}: ${item.msg}` : item.msg;
    });
    return messages.length ? messages.join("; ") : fallback;
  }
  if (detail && typeof detail.message === "string") {
    return detail.message;
  }
  return fallback;
}

export async function errorDetail(response) {
  const fallback = `Request failed with status ${response.status}`;
  try {
    const payload = await response.json();
    return normalizedErrorDetail(payload.detail, fallback);
  } catch {
    return fallback;
  }
}

export function generateUuid() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiRequest(url, options = {}) {
  const headers = new Headers(authHeaders());
  new Headers(options.headers).forEach((value, name) => headers.set(name, value));
  options.signal?.throwIfAborted();
  const response = await fetch(url, { ...options, headers });
  options.signal?.throwIfAborted();
  if (!response.ok) {
    const detail = await errorDetail(response);
    options.signal?.throwIfAborted();
    throw new ApiError(response.status, detail);
  }
  return response;
}

export async function apiJson(url, options = {}) {
  const response = await apiRequest(url, options);
  if (response.status === 204) return null;
  let payload;
  try {
    payload = await response.json();
  } catch {
    options.signal?.throwIfAborted();
    throw new ApiError(response.status, "Unable to parse server response.");
  }
  options.signal?.throwIfAborted();
  return payload;
}
