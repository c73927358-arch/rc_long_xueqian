// Shared formatting, JSON, API key, fetch, and message helpers.
function showMessage(text, tone = "ok") {
  message.hidden = false;
  message.textContent = text;
  message.style.background = tone === "error" ? "#feecea" : "#e6f4f1";
  message.style.color = tone === "error" ? "#b42318" : "#115e59";
  clearTimeout(message.hideTimer);
  message.hideTimer = setTimeout(() => {
    message.hidden = true;
  }, 3500);
}

function parseJsonField(value, fallback) {
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed);
}

function setBodyJsonError(text = "") {
  bodyJsonError.hidden = !text;
  bodyJsonError.textContent = text;
  bodyTextarea.setAttribute("aria-invalid", text ? "true" : "false");
}

function parseBodyJsonField() {
  try {
    const parsed = parseJsonField(bodyTextarea.value || "", {});
    setBodyJsonError();
    return parsed;
  } catch (error) {
    setBodyJsonError(`Body JSON 格式错误：${error.message}`);
    throw error;
  }
}

function formatBodyJson() {
  try {
    bodyTextarea.value = JSON.stringify(parseBodyJsonField(), null, 2);
    setBodyJsonError();
    showMessage("Body JSON 已格式化");
  } catch (error) {
    showMessage(`Body JSON 格式错误：${error.message}`, "error");
  }
}

function timestampId(prefix) {
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  return `${prefix}-${stamp}`;
}

function localTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function formatJson(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function isSensitiveHeader(key) {
  const lowerKey = String(key).toLowerCase();
  return ["token", "authorization", "secret", "password", "key"].some((word) => lowerKey.includes(word));
}

function maskHeaders(headers) {
  const masked = {};
  for (const [key, value] of Object.entries(headers || {})) {
    masked[key] = isSensitiveHeader(key) ? "******" : value;
  }
  return masked;
}

function currentApiKey() {
  return String(apiKeyInput?.value || "").trim();
}

function loadApiKey() {
  if (!apiKeyInput) return;
  try {
    apiKeyInput.value = sessionStorage.getItem(apiKeyStorageKey) || "";
  } catch {
    apiKeyInput.value = "";
  }
}

function persistApiKey() {
  if (!apiKeyInput) return;
  const apiKey = currentApiKey();
  try {
    if (apiKey) {
      sessionStorage.setItem(apiKeyStorageKey, apiKey);
    } else {
      sessionStorage.removeItem(apiKeyStorageKey);
    }
  } catch {
    // sessionStorage can be unavailable in restricted browser contexts.
  }
}

function withApiKeyHeader(options = {}) {
  const apiKey = currentApiKey();
  if (!apiKey) return options;
  const headers = new Headers(options.headers || {});
  headers.set(apiKeyHeaderName, apiKey);
  return { ...options, headers };
}

function fetchWithApiKey(path, options = {}) {
  return fetch(path, withApiKeyHeader(options));
}

function payloadErrorText(payload = {}) {
  const value = payload.error ?? payload.detail ?? payload.message;
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function httpErrorMessage(response, payload = {}) {
  const detail = payloadErrorText(payload);
  if (response.status === 401) {
    return detail ? `认证失败（401）：${detail}` : "认证失败（401）：请检查 API Key";
  }
  return detail || `请求失败：${response.status}`;
}

async function fetchJson(path, options) {
  const response = await fetchWithApiKey(path, options);
  let result = {};
  try {
    result = await response.json();
  } catch {
    result = {};
  }
  if (!response.ok) {
    const error = new Error(httpErrorMessage(response, result));
    error.status = response.status;
    throw error;
  }
  return result;
}

function firstPresent(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}
