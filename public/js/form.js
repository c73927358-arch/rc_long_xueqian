// Notification submit, curl-copy, and auto-refresh commands.
function buildNotificationPayload() {
  const data = new FormData(form);
  const requestId = String(data.get("requestId") || "").trim();
  const timeoutSeconds = String(data.get("timeoutSeconds") || "").trim();
  const payload = {
    eventType: data.get("eventType"),
    sourceSystem: data.get("sourceSystem"),
    targetUrl: data.get("targetUrl"),
    method: data.get("method"),
    maxAttempts: Number(data.get("maxAttempts") || 5),
    headers: parseJsonField(data.get("headers") || "", {}),
    body: parseBodyJsonField(),
  };
  if (requestId) payload.requestId = requestId;
  if (timeoutSeconds) payload.timeoutSeconds = Number(timeoutSeconds);
  return payload;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function buildCurlCommand(payload) {
  const parts = [
    "curl",
    "-X",
    "POST",
    shellQuote(`${window.location.origin}/api/notifications`),
  ];
  const apiKey = currentApiKey();
  if (apiKey) {
    parts.push("-H", shellQuote(`${apiKeyHeaderName}: ${apiKey}`));
  }
  parts.push(
    "-H",
    shellQuote("Content-Type: application/json"),
    "-d",
    shellQuote(JSON.stringify(payload, null, 2))
  );
  return parts.join(" ");
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

async function copyValue(label, value) {
  const normalized = value === null || value === undefined ? "" : String(value).trim();
  if (!normalized) {
    showMessage(`${label} 为空，无法复制`, "error");
    return;
  }
  try {
    await copyText(normalized);
    showMessage(`${label} 已复制`);
  } catch (error) {
    showMessage(`复制失败：${error.message}`, "error");
  }
}

async function copyCurl() {
  if (form.reportValidity && !form.reportValidity()) return;
  let payload;
  try {
    payload = buildNotificationPayload();
  } catch (error) {
    showMessage(`JSON 格式错误：${error.message}`, "error");
    return;
  }
  try {
    await copyText(buildCurlCommand(payload));
    showMessage("curl 已复制");
  } catch (error) {
    showMessage(`复制失败：${error.message}`, "error");
  }
}

async function submitNotification(event) {
  event.preventDefault();
  if (form.reportValidity && !form.reportValidity()) return;
  let payload;
  try {
    payload = buildNotificationPayload();
  } catch (error) {
    showMessage(`JSON 格式错误：${error.message}`, "error");
    return;
  }

  let result;
  try {
    result = await fetchJson("/api/notifications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    showMessage(error.message || "提交失败", "error");
    return;
  }
  resetPagination();
  showMessage(`已入队：${result.id}`);
  await loadNotifications();
}

function setAutoRefreshPaused(paused) {
  autoRefreshPaused = paused;
  autoRefreshToggle.textContent = paused ? "恢复自动刷新" : "暂停自动刷新";
  autoRefreshToggle.setAttribute("aria-pressed", String(paused));
}

function tickAutoRefresh() {
  if (!autoRefreshPaused) loadNotifications();
}
