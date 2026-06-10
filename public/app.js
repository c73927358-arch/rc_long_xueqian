const form = document.querySelector("#notificationForm");
const list = document.querySelector("#notificationList");
const template = document.querySelector("#notificationTemplate");
const message = document.querySelector("#message");
const refreshButton = document.querySelector("#refreshButton");
const apiKeyInput = document.querySelector("#apiKeyInput");
const autoRefreshToggle = document.querySelector("#autoRefreshToggle");
const retryFailedBatchButton = document.querySelector("#retryFailedBatch");
const filterForm = document.querySelector("#notificationFilters");
const clearFiltersButton = document.querySelector("#clearFilters");
const exportCsvButton = document.querySelector("#exportCsv");
const paginationSummary = document.querySelector("#paginationSummary");
const prevPageButton = document.querySelector("#prevPage");
const nextPageButton = document.querySelector("#nextPage");
const summaryStats = document.querySelector("#summaryStats");
const deliveryStats = document.querySelector("#deliveryStats");
const healthBar = document.querySelector("#healthBar");
const healthSummary = document.querySelector("#healthSummary");
const refreshHealthButton = document.querySelector("#refreshHealth");
const statusTabs = document.querySelectorAll(".status-tabs button");
const fillSuccess = document.querySelector("#fillSuccess");
const fillFailure = document.querySelector("#fillFailure");
const copyCurlButton = document.querySelector("#copyCurl");
const bodyTextarea = form.elements.body;
const formatBodyJsonButton = document.querySelector("#formatBodyJson");
const bodyJsonError = document.querySelector("#bodyJsonError");
const detailModal = document.querySelector("#detailModal");
const detailPanel = document.querySelector(".detail-panel");
const detailContent = document.querySelector("#detailContent");
const detailLoading = document.querySelector("#detailLoading");
const detailSubtitle = document.querySelector("#detailSubtitle");
const closeDetailButton = document.querySelector("#closeDetail");
const refreshDetailButton = document.querySelector("#refreshDetail");
const retryDetailButton = document.querySelector("#retryDetail");
const deadLetterDetailButton = document.querySelector("#deadLetterDetail");
const copyDetailIdButton = document.querySelector("#copyDetailId");
const copyDetailTargetUrlButton = document.querySelector("#copyDetailTargetUrl");
const copyDetailRequestIdButton = document.querySelector("#copyDetailRequestId");
const attemptList = document.querySelector("#attemptList");
const manualHandledByInput = document.querySelector("#manualHandledBy");
const manualNoteInput = document.querySelector("#manualNote");

let currentStatus = "";
let refreshTimer = null;
let healthTimer = null;
let currentDetailId = null;
let currentDetailItem = null;
let autoRefreshPaused = false;
let lastHealthLoadedAt = 0;
let lastStatsLoadedAt = 0;
let currentOffset = 0;
let lastPagination = null;
let lastFilterErrorText = "";

const apiKeyStorageKey = "notificationApiKey";
const apiKeyHeaderName = "X-Notification-Api-Key";
const sortFields = new Set(["createdAt", "updatedAt", "nextAttemptAt"]);
const sortOrders = new Set(["desc", "asc"]);
const timeFilterFields = ["createdFrom", "createdTo", "updatedFrom", "updatedTo"];
const timeFilterRanges = [
  ["createdFrom", "createdTo", "创建时间"],
  ["updatedFrom", "updatedTo", "更新时间"],
];

const statusLabels = {
  queued: "等待投递",
  delivering: "投递中",
  waiting_retry: "等待重试",
  succeeded: "投递成功",
  failed: "最终失败",
  dead_letter: "死信/人工",
};

const attemptStatusLabels = {
  succeeded: "本次成功",
  failed: "本次失败",
  delivering: "本次投递中",
  dead_letter: "已转人工",
};

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

function pickServiceVersion(raw = {}) {
  const service = raw.service || raw.application || raw.app || {};
  const metadata = raw.metadata || raw.meta || {};
  return firstPresent(
    raw.serviceVersion,
    raw.service_version,
    raw.appVersion,
    raw.app_version,
    raw.version,
    service.version,
    service.serviceVersion,
    service.service_version,
    metadata.serviceVersion,
    metadata.service_version
  );
}

function pickSchemaVersion(raw = {}) {
  const database = raw.database || raw.db || {};
  const schema = raw.schema || {};
  const metadata = raw.metadata || raw.meta || {};
  return firstPresent(
    raw.schemaVersion,
    raw.schema_version,
    raw.dbSchemaVersion,
    raw.db_schema_version,
    schema.version,
    schema.schemaVersion,
    schema.schema_version,
    database.schemaVersion,
    database.schema_version,
    metadata.schemaVersion,
    metadata.schema_version
  );
}

function setHealthMetric(name, value) {
  const node = healthBar.querySelector(`[data-health="${name}"]`);
  if (node) node.textContent = String(firstPresent(value, "-"));
}

function setStatsMetric(name, value) {
  const node = deliveryStats.querySelector(`[data-stats="${name}"]`);
  if (node) node.textContent = String(firstPresent(value, "-"));
}

function boolLabel(value) {
  if (value === true) return "OK";
  if (value === false) return "异常";
  return "未知";
}

function normalizeBool(value) {
  if (value === true || value === false || value === undefined) return value;
  if (value === 1 || value === "1") return true;
  if (value === 0 || value === "0") return false;
  const normalized = String(value).toLowerCase();
  if (["true", "ok", "healthy", "up", "alive", "connected", "running"].includes(normalized)) return true;
  if (["false", "error", "down", "dead", "disconnected", "stopped"].includes(normalized)) return false;
  return undefined;
}

function isHealthyStatus(value) {
  const normalized = String(value || "").toLowerCase();
  return ["ok", "healthy", "ready", "up", "success", "running"].includes(normalized);
}

function isUnhealthyStatus(value) {
  const normalized = String(value || "").toLowerCase();
  return Boolean(normalized) && !isHealthyStatus(normalized);
}

function normalizeHealth(raw) {
  const database = raw.database || raw.db || {};
  const worker = raw.worker || {};
  const queue = raw.queue || raw.queues || {};
  const workerConfig = worker.config || worker.settings || raw.workerConfig || raw.worker_config || {};
  const workerRuntime = worker.runtime || raw.workerRuntime || raw.worker_runtime || {};
  const workerPool = worker.pool || worker.threadPool || worker.thread_pool || raw.workerPool || raw.worker_pool || raw.threadPool || raw.thread_pool || {};
  const workerThreads = worker.threads && typeof worker.threads === "object" ? worker.threads : {};
  const rawThreads = raw.threads && typeof raw.threads === "object" ? raw.threads : {};
  const workerThreadsValue = worker.threads && typeof worker.threads !== "object" ? worker.threads : undefined;
  const rawThreadsValue = raw.threads && typeof raw.threads !== "object" ? raw.threads : undefined;
  const counts = raw.counts || queue.counts || raw.statusCounts || raw.notificationCounts || worker.counts || {};
  const status = firstPresent(raw.status, raw.health, raw.state, raw.ok === false ? "error" : undefined, "unknown");
  const serviceVersion = pickServiceVersion(raw);
  const schemaVersion = pickSchemaVersion(raw);
  const databaseOk = normalizeBool(firstPresent(database.ok, database.alive, database.connected, raw.databaseOk, raw.dbOk));
  const workerAlive = normalizeBool(firstPresent(worker.alive, worker.ok, worker.running, raw.workerAlive, raw.workerOk));
  const concurrency = firstPresent(
    raw.concurrency,
    raw.workerConcurrency,
    raw.worker_concurrency,
    worker.concurrency,
    worker.maxConcurrency,
    worker.max_concurrency,
    workerConfig.concurrency,
    workerConfig.maxConcurrency,
    workerConfig.max_concurrency,
    workerRuntime.concurrency,
    workerRuntime.maxConcurrency,
    workerRuntime.max_concurrency,
    workerPool.concurrency,
    workerPool.maxConcurrency,
    workerPool.max_concurrency
  );
  const threadCount = firstPresent(
    raw.threadCount,
    raw.thread_count,
    raw.workerThreadCount,
    raw.worker_thread_count,
    rawThreads.threadCount,
    rawThreads.thread_count,
    rawThreads.count,
    rawThreads.total,
    rawThreadsValue,
    worker.threadCount,
    worker.thread_count,
    workerThreads.threadCount,
    workerThreads.thread_count,
    workerThreads.count,
    workerThreads.total,
    workerThreadsValue,
    workerRuntime.threadCount,
    workerRuntime.thread_count,
    workerPool.threadCount,
    workerPool.thread_count,
    workerPool.size,
    workerPool.maxThreads,
    workerPool.max_threads
  );
  const aliveCount = firstPresent(
    raw.aliveCount,
    raw.alive_count,
    raw.workerAliveCount,
    raw.worker_alive_count,
    rawThreads.aliveCount,
    rawThreads.alive_count,
    rawThreads.alive,
    rawThreads.activeCount,
    rawThreads.active_count,
    worker.aliveCount,
    worker.alive_count,
    worker.aliveThreads,
    worker.alive_threads,
    workerThreads.aliveCount,
    workerThreads.alive_count,
    workerThreads.alive,
    workerThreads.activeCount,
    workerThreads.active_count,
    workerRuntime.aliveCount,
    workerRuntime.alive_count,
    workerPool.aliveCount,
    workerPool.alive_count,
    workerPool.alive,
    workerPool.activeCount,
    workerPool.active_count
  );
  const queued = firstPresent(raw.queued, counts.queued, worker.queued);
  const waitingRetry = firstPresent(
    raw.waiting_retry,
    raw.waitingRetry,
    counts.waiting_retry,
    counts.waitingRetry,
    worker.waiting_retry,
    worker.waitingRetry
  );
  const failed = firstPresent(raw.failed, counts.failed, worker.failed);
  const readyCount = firstPresent(
    raw.readyCount,
    raw.ready_count,
    queue.readyCount,
    queue.ready_count,
    counts.readyCount,
    counts.ready_count,
    counts.ready,
    worker.readyCount,
    worker.ready_count
  );
  const deliveringLeaseSeconds = firstPresent(
    worker.deliveringLeaseSeconds,
    worker.delivering_lease_seconds,
    raw.deliveringLeaseSeconds,
    raw.delivering_lease_seconds
  );
  const lastLeaseRecoveryCount = firstPresent(
    worker.lastLeaseRecoveryCount,
    worker.last_lease_recovery_count,
    raw.lastLeaseRecoveryCount,
    raw.last_lease_recovery_count
  );
  const expiredDeliveringCount = firstPresent(
    queue.expiredDeliveringCount,
    queue.expired_delivering_count,
    raw.expiredDeliveringCount,
    raw.expired_delivering_count,
    counts.expiredDeliveringCount,
    counts.expired_delivering_count,
    worker.expiredDeliveringCount,
    worker.expired_delivering_count
  );

  const hasProblem = isUnhealthyStatus(status) || databaseOk === false || workerAlive === false;
  const hasUnknown = status === "unknown" || databaseOk === undefined || workerAlive === undefined;
  return {
    status,
    serviceVersion,
    schemaVersion,
    databaseOk,
    workerAlive,
    concurrency,
    threadCount,
    aliveCount,
    readyCount,
    queued,
    waitingRetry,
    failed,
    deliveringLeaseSeconds,
    lastLeaseRecoveryCount,
    expiredDeliveringCount,
    hasProblem,
    hasUnknown,
  };
}

function renderHealth(raw) {
  const health = normalizeHealth(raw || {});
  healthBar.classList.remove("is-ok", "is-warning", "is-danger", "is-unknown");
  healthBar.classList.add(health.hasProblem ? "is-danger" : health.hasUnknown ? "is-warning" : "is-ok");
  healthSummary.textContent = health.hasProblem ? "健康异常，请检查数据库或 worker" : health.hasUnknown ? "健康字段不完整，已按未知展示" : "服务运行正常";
  setHealthMetric("status", health.status);
  setHealthMetric("serviceVersion", health.serviceVersion);
  setHealthMetric("schemaVersion", health.schemaVersion);
  setHealthMetric("database", boolLabel(health.databaseOk));
  setHealthMetric("worker", boolLabel(health.workerAlive));
  setHealthMetric("concurrency", health.concurrency);
  setHealthMetric("threadCount", health.threadCount);
  setHealthMetric("aliveCount", health.aliveCount);
  setHealthMetric("readyCount", health.readyCount);
  setHealthMetric("queued", health.queued);
  setHealthMetric("waitingRetry", health.waitingRetry);
  setHealthMetric("failed", health.failed);
  setHealthMetric("deliveringLeaseSeconds", health.deliveringLeaseSeconds);
  setHealthMetric("lastLeaseRecoveryCount", health.lastLeaseRecoveryCount);
  setHealthMetric("expiredDeliveringCount", health.expiredDeliveringCount);
}

async function loadHealth(options = {}) {
  const now = Date.now();
  const throttleMs = options.throttleMs ?? 0;
  if (!options.force && throttleMs && now - lastHealthLoadedAt < throttleMs) return;
  lastHealthLoadedAt = now;
  refreshHealthButton.disabled = true;
  try {
    renderHealth(await fetchJson("/health"));
  } catch (error) {
    healthBar.classList.remove("is-ok", "is-warning", "is-unknown");
    healthBar.classList.add("is-danger");
    healthSummary.textContent = error.message || "健康检查失败";
    setHealthMetric("status", "error");
    setHealthMetric("serviceVersion", "-");
    setHealthMetric("schemaVersion", "-");
    setHealthMetric("database", "未知");
    setHealthMetric("worker", "未知");
    setHealthMetric("concurrency", "-");
    setHealthMetric("threadCount", "-");
    setHealthMetric("aliveCount", "-");
    setHealthMetric("readyCount", "-");
    setHealthMetric("queued", "-");
    setHealthMetric("waitingRetry", "-");
    setHealthMetric("failed", "-");
    setHealthMetric("deliveringLeaseSeconds", "-");
    setHealthMetric("lastLeaseRecoveryCount", "-");
    setHealthMetric("expiredDeliveringCount", "-");
    if (!options.silent) showMessage(healthSummary.textContent, "error");
  } finally {
    refreshHealthButton.disabled = false;
  }
}

function sumPresent(...values) {
  let total = 0;
  let hasValue = false;
  for (const value of values) {
    if (value === undefined || value === null || value === "") continue;
    const number = Number(value);
    if (Number.isNaN(number)) continue;
    total += number;
    hasValue = true;
  }
  return hasValue ? total : undefined;
}

function normalizeStats(raw) {
  const queue = raw.queue || raw.queues || {};
  const worker = raw.worker || {};
  const notifications = raw.notifications || raw.notification || {};
  const counts = raw.counts || queue.counts || raw.statusCounts || raw.notificationCounts || {};
  const queued = firstPresent(raw.queued, queue.queued, counts.queued);
  const waitingRetry = firstPresent(raw.waiting_retry, raw.waitingRetry, queue.waitingRetry, counts.waiting_retry, counts.waitingRetry);
  const pending = firstPresent(raw.pending, raw.pendingCount, queue.pending, counts.pending, sumPresent(queued, waitingRetry));
  return {
    total: firstPresent(raw.total, raw.totalCount, raw.count, notifications.total, notifications.totalCount, counts.total, counts.all),
    serviceVersion: pickServiceVersion(raw),
    schemaVersion: pickSchemaVersion(raw),
    pending,
    succeeded: firstPresent(raw.succeeded, raw.success, raw.successCount, counts.succeeded, counts.success),
    failed: firstPresent(raw.failed, raw.failedCount, counts.failed),
    deadLetter: firstPresent(raw.dead_letter, raw.deadLetter, raw.deadLetterCount, counts.dead_letter, counts.deadLetter, counts.deadLetterCount),
    delivering: firstPresent(raw.delivering, raw.deliveringCount, counts.delivering, worker.delivering),
    expiredDeliveringCount: firstPresent(
      raw.expiredDeliveringCount,
      raw.expired_delivering_count,
      queue.expiredDeliveringCount,
      queue.expired_delivering_count,
      counts.expiredDeliveringCount,
      counts.expired_delivering_count,
      worker.expiredDeliveringCount,
      worker.expired_delivering_count
    ),
  };
}

function renderStats(raw) {
  const stats = normalizeStats(raw || {});
  deliveryStats.hidden = false;
  for (const [key, value] of Object.entries(stats)) {
    setStatsMetric(key, value);
  }
}

function hideStats() {
  deliveryStats.hidden = true;
  for (const node of deliveryStats.querySelectorAll("[data-stats]")) {
    node.textContent = "-";
  }
}

async function loadStats(options = {}) {
  const now = Date.now();
  const throttleMs = options.throttleMs ?? 0;
  if (!options.force && throttleMs && now - lastStatsLoadedAt < throttleMs) return;
  lastStatsLoadedAt = now;
  try {
    renderStats(await fetchJson("/api/stats"));
  } catch {
    hideStats();
  }
}

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

function getFilterValue(name) {
  return String(filterForm.elements[name]?.value || "").trim();
}

function normalizedLimit(value) {
  const limit = Number.parseInt(value, 10);
  if (Number.isNaN(limit)) return "";
  return String(Math.min(Math.max(limit, 1), 200));
}

function currentLimit() {
  return Number.parseInt(normalizedLimit(getFilterValue("limit")) || "50", 10);
}

function normalizeSortBy(value) {
  return sortFields.has(value) ? value : "createdAt";
}

function normalizeSortOrder(value) {
  return sortOrders.has(value) ? value : "desc";
}

function parseTimeFilterValue(value) {
  if (!value) return undefined;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : Number.NaN;
}

function timeFilterError() {
  for (const [fromName, toName, label] of timeFilterRanges) {
    const fromValue = getFilterValue(fromName);
    const toValue = getFilterValue(toName);
    const fromTime = parseTimeFilterValue(fromValue);
    const toTime = parseTimeFilterValue(toValue);
    if (Number.isNaN(fromTime)) return `${label}开始时间格式无效`;
    if (Number.isNaN(toTime)) return `${label}结束时间格式无效`;
    if (fromTime !== undefined && toTime !== undefined && fromTime > toTime) {
      return `${label}范围无效：开始时间不能晚于结束时间`;
    }
  }
  return "";
}

function normalizedTimeFilterValue(name) {
  const value = getFilterValue(name);
  if (!value) return "";
  const timestamp = parseTimeFilterValue(value);
  if (Number.isNaN(timestamp)) return value;
  return new Date(timestamp).toISOString();
}

function hasActiveTimeFilters() {
  return timeFilterFields.some((name) => getFilterValue(name));
}

function showFilterError(text) {
  if (text === lastFilterErrorText) return;
  lastFilterErrorText = text;
  showMessage(text, "error");
}

function resetFilterErrorState() {
  lastFilterErrorText = "";
}

function queryErrorMessage(action, error) {
  const detail = error?.message || "请求失败";
  const prefix = action.includes(" ") ? `${action} 失败` : `${action}失败`;
  if (hasActiveTimeFilters()) {
    return `${prefix}：${detail}。时间范围筛选可能尚未被后端支持，请清空 createdFrom/createdTo/updatedFrom/updatedTo 后重试。`;
  }
  return `${prefix}：${detail}`;
}

function buildNotificationQuery(options = {}) {
  const includePaging = options.includePaging ?? true;
  const includeLimit = options.includeLimit ?? includePaging;
  const includeSort = options.includeSort ?? true;
  const query = new URLSearchParams();
  if (currentStatus) query.set("status", currentStatus);
  for (const name of ["eventType", "sourceSystem", "targetUrl"]) {
    const value = getFilterValue(name);
    if (value) query.set(name, value);
  }
  for (const name of timeFilterFields) {
    const value = normalizedTimeFilterValue(name);
    if (value) query.set(name, value);
  }
  if (includeSort) {
    query.set("sort", normalizeSortBy(getFilterValue("sortBy")));
    query.set("order", normalizeSortOrder(getFilterValue("sortOrder")));
  }
  const limit = normalizedLimit(getFilterValue("limit"));
  if (limit) query.set("limit", limit);
  if (includePaging) query.set("offset", String(Math.max(currentOffset, 0)));
  if (!includeLimit) query.delete("limit");
  return query;
}

function resetPagination() {
  currentOffset = 0;
  lastPagination = null;
}

function numberOrUndefined(value) {
  if (value === undefined || value === null || value === "") return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function normalizePagination(raw, itemCount) {
  const source = raw || {};
  const limit = numberOrUndefined(firstPresent(source.limit, source.pageSize, source.perPage)) ?? currentLimit();
  const offset = numberOrUndefined(firstPresent(source.offset, source.start, source.skip)) ?? currentOffset;
  const total = numberOrUndefined(firstPresent(source.total, source.totalCount));
  const nextOffset = numberOrUndefined(firstPresent(source.nextOffset, source.next_offset));
  const previousOffset = numberOrUndefined(firstPresent(source.previousOffset, source.prevOffset, source.previous_offset, source.prev_offset));
  const hasNext = normalizeBool(firstPresent(source.hasNext, source.has_next, source.hasMore, source.has_more, nextOffset !== undefined ? true : undefined));
  const hasPrevious = normalizeBool(firstPresent(source.hasPrevious, source.has_previous, previousOffset !== undefined ? true : undefined));

  return {
    supported: Boolean(raw),
    limit,
    offset,
    total,
    itemCount,
    nextOffset,
    previousOffset,
    hasNext: hasNext === undefined ? (total === undefined ? itemCount >= limit : offset + itemCount < total) : Boolean(hasNext),
    hasPrevious: hasPrevious === undefined ? offset > 0 : Boolean(hasPrevious),
  };
}

function normalizeNotificationListResponse(result) {
  if (Array.isArray(result)) {
    return { items: result, pagination: normalizePagination(null, result.length) };
  }

  const items = Array.isArray(result?.items) ? result.items : Array.isArray(result?.data) ? result.data : [];
  const paginationSource = result?.pagination || result?.page || result?.meta?.pagination || null;
  return { items, pagination: normalizePagination(paginationSource, items.length) };
}

function renderPagination(pagination) {
  lastPagination = pagination;
  const page = Math.floor(pagination.offset / Math.max(pagination.limit, 1)) + 1;
  const parts = [`第 ${page} 页`, `offset ${pagination.offset}`, `每页 ${pagination.limit}`, `当前 ${pagination.itemCount} 条`];
  if (pagination.total !== undefined) parts.push(`总数 ${pagination.total}`);
  if (!pagination.supported) parts.push("旧响应未返回分页信息");
  paginationSummary.textContent = parts.join(" · ");
  prevPageButton.disabled = !pagination.hasPrevious;
  nextPageButton.disabled = !pagination.hasNext;
}

function setOffsetAndLoad(offset) {
  currentOffset = Math.max(0, offset);
  loadNotifications();
}

async function readResponseError(response) {
  const fallback = response.status === 401 ? "认证失败（401）：请检查 API Key" : `请求失败：${response.status}`;
  try {
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.includes("application/json")) {
      const result = await response.json();
      return httpErrorMessage(response, result);
    }
    const text = (await response.text()).trim();
    if (!text) return fallback;
    const detail = text.slice(0, 300);
    return response.status === 401 ? `认证失败（401）：${detail}` : detail;
  } catch {
    return fallback;
  }
}

function csvFilename(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  if (!match) return "notifications.csv";
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

async function openCsvExport() {
  const validationError = timeFilterError();
  if (validationError) {
    showFilterError(validationError);
    return;
  }
  resetFilterErrorState();
  const query = buildNotificationQuery({ includePaging: false, includeLimit: false });
  const url = `/api/notifications/export.csv${query.toString() ? `?${query}` : ""}`;
  exportCsvButton.disabled = true;
  try {
    const response = await fetchWithApiKey(url);
    if (!response.ok) throw new Error(await readResponseError(response));
    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = downloadUrl;
    anchor.download = csvFilename(response);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    showMessage("CSV 已开始下载");
  } catch (error) {
    showMessage(queryErrorMessage("导出 CSV", error), "error");
  } finally {
    exportCsvButton.disabled = false;
  }
}

async function loadNotifications() {
  const validationError = timeFilterError();
  if (validationError) {
    showFilterError(validationError);
    return;
  }
  resetFilterErrorState();
  const query = buildNotificationQuery();
  const queryString = query.toString();
  try {
    loadHealth({ silent: true, throttleMs: 10000 });
    loadStats({ throttleMs: 10000 });
    const listResult = await fetchJson(`/api/notifications${queryString ? `?${queryString}` : ""}`);
    const { items, pagination } = normalizeNotificationListResponse(listResult);
    renderSummary(items);
    renderList(items);
    renderPagination(pagination);
  } catch (error) {
    showMessage(queryErrorMessage("加载任务", error), "error");
  }
}

function renderSummary(items) {
  const counts = {
    all: items.length,
    pending: 0,
    succeeded: 0,
    failed: 0,
    deadLetter: 0,
    delivering: 0,
  };

  for (const item of items) {
    if (item.status === "queued" || item.status === "waiting_retry") counts.pending += 1;
    if (item.status === "succeeded") counts.succeeded += 1;
    if (item.status === "failed") counts.failed += 1;
    if (item.status === "dead_letter") counts.deadLetter += 1;
    if (item.status === "delivering") counts.delivering += 1;
  }

  for (const [key, value] of Object.entries(counts)) {
    const node = summaryStats.querySelector(`[data-summary="${key}"]`);
    if (node) node.textContent = String(value);
  }
}

function renderList(items) {
  list.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无任务";
    list.append(empty);
    return;
  }

  for (const item of items) {
    const node = template.content.cloneNode(true);
    const card = node.querySelector(".notification-card");
    const status = node.querySelector(".status-pill");
    node.querySelector(".event-type").textContent = item.eventType || "未命名事件";
    node.querySelector(".target-url").textContent = item.targetUrl;
    node.querySelector(".source-system").textContent = item.sourceSystem || "-";
    node.querySelector(".attempts").textContent = `${item.attemptCount}/${item.maxAttempts}`;
    node.querySelector(".created-at").textContent = localTime(item.createdAt);
    node.querySelector(".next-at").textContent = localTime(item.nextAttemptAt);
    node.querySelector(".error-line").textContent = item.lastError || "";
    status.textContent = statusLabels[item.status] || item.status;
    status.className = `status-pill ${item.status}`;
    const deadLetterButton = node.querySelector(".dead-letter-button");
    configureDeadLetterButton(deadLetterButton, item, { hideWhenUnavailable: true });

    node.querySelector(".detail-button").addEventListener("click", () => openDetail(item.id));
    node.querySelector(".retry-button").addEventListener("click", () => retryNotification(item.id));
    deadLetterButton.addEventListener("click", () => markDeadLetter(item.id, { button: deadLetterButton, handledBy: "operator-ui", note: "" }));
    node.querySelector(".copy-button").addEventListener("click", async () => {
      await navigator.clipboard.writeText(item.id);
      showMessage("任务 ID 已复制");
    });

    card.dataset.id = item.id;
    list.append(node);
  }
}

function canMarkDeadLetter(item) {
  return item?.status === "failed" || item?.status === "waiting_retry";
}

function configureDeadLetterButton(button, item, options = {}) {
  if (!button) return;
  const alreadyDeadLetter = item?.status === "dead_letter";
  const allowed = canMarkDeadLetter(item);
  const hideWhenUnavailable = options.hideWhenUnavailable ?? false;
  button.hidden = hideWhenUnavailable && !allowed && !alreadyDeadLetter;
  button.disabled = !allowed;
  button.textContent = alreadyDeadLetter ? "已转人工" : "标记死信/转人工";
  button.title = allowed
    ? "将该任务标记为死信并交给人工处理"
    : alreadyDeadLetter
      ? "任务已进入死信/人工状态"
      : "仅失败或等待重试任务可转人工";
}

async function retryNotification(id, options = {}) {
  let result;
  try {
    result = await fetchJson(`/api/notifications/${id}/retry`, { method: "POST" });
  } catch (error) {
    showMessage(error.message || "重试失败", "error");
    return;
  }
  showMessage(`已重新入队：${result.id}`);
  await Promise.all([
    loadNotifications(),
    options.refreshDetail ? loadDetail(id, { silent: true }) : Promise.resolve(),
  ]);
}

async function retryFailedBatch() {
  const limit = Number.parseInt(normalizedLimit(getFilterValue("limit")) || "50", 10);
  retryFailedBatchButton.disabled = true;
  try {
    const result = await fetchJson("/api/notifications/retry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "failed", limit }),
    });
    const count = result.count ?? result.retriedCount ?? result.updatedCount ?? result.items?.length ?? 0;
    showMessage(`已批量重试失败任务：${count} 条`);
    await loadNotifications();
  } catch (error) {
    showMessage(error.message || "批量重试失败", "error");
  } finally {
    retryFailedBatchButton.disabled = false;
  }
}

function buildManualActionPayload(options = {}) {
  const hasHandledBy = Object.prototype.hasOwnProperty.call(options, "handledBy");
  const hasNote = Object.prototype.hasOwnProperty.call(options, "note");
  const handledBySource = hasHandledBy ? options.handledBy : manualHandledByInput?.value;
  const noteSource = hasNote ? options.note : manualNoteInput?.value;
  const handledBy = String(firstPresent(handledBySource, "operator-ui")).trim() || "operator-ui";
  const note = String(noteSource ?? "").trim();
  const payload = { handledBy };
  if (note) payload.note = note;
  return payload;
}

async function markDeadLetter(id, options = {}) {
  if (!id) return;
  const button = options.button;
  if (button) button.disabled = true;
  try {
    const result = await fetchJson(`/api/notifications/${id}/dead-letter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildManualActionPayload(options)),
    });
    showMessage(`已转人工：${result.id || id}`);
    await Promise.all([
      loadNotifications(),
      loadStats({ force: true }),
      options.refreshDetail ? loadDetail(id, { silent: true }) : Promise.resolve(),
    ]);
  } catch (error) {
    const prefix = error.status ? `HTTP ${error.status}` : "接口错误";
    showMessage(`转人工失败（${prefix}）：${error.message}`, "error");
  } finally {
    if (button === deadLetterDetailButton) {
      configureDeadLetterButton(button, currentDetailItem, { hideWhenUnavailable: false });
    } else if (button) {
      button.disabled = false;
    }
  }
}

function setDetailText(selector, value) {
  document.querySelector(selector).textContent = value === null || value === undefined || value === "" ? "-" : String(value);
}

function detailValue(item, ...names) {
  if (!item) return undefined;
  return firstPresent(...names.map((name) => item[name]));
}

function detailTimeoutSeconds(item) {
  const value = detailValue(item, "timeoutSeconds", "timeout_seconds");
  return value === undefined || value === null || value === "" ? "默认" : String(value);
}

function setDetailCopyButtons(item) {
  copyDetailIdButton.disabled = !detailValue(item, "id", "notificationId", "notification_id");
  copyDetailTargetUrlButton.disabled = !detailValue(item, "targetUrl", "target_url");
  copyDetailRequestIdButton.disabled = !detailValue(item, "requestId", "request_id");
}

function renderAttempts(items) {
  attemptList.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state compact";
    empty.textContent = "暂无尝试记录";
    attemptList.append(empty);
    return;
  }

  const summary = document.createElement("div");
  summary.className = "attempt-summary";
  summary.textContent = `共 ${items.length} 条尝试记录，按接口返回顺序展示`;
  attemptList.append(summary);

  for (const attempt of items) {
    const item = document.createElement("article");
    item.className = "attempt-item";

    const top = document.createElement("div");
    top.className = "attempt-top";

    const number = document.createElement("p");
    number.className = "attempt-number";
    number.textContent = `全局序号 #${attempt.attemptSequence ?? "-"}`;

    const status = document.createElement("span");
    status.className = `status-pill ${attempt.status || ""}`;
    status.textContent = attemptStatusLabels[attempt.status] || attempt.status || "-";

    top.append(number, status);

    const meta = document.createElement("dl");
    meta.className = "attempt-meta";

    const fields = [
      ["全局尝试序号", attempt.attemptSequence ?? "-"],
      ["投递轮次", attempt.deliveryRun ?? "-"],
      ["本轮次数", attempt.attemptNumber ?? "-"],
      ["本次结果", attemptStatusLabels[attempt.status] || attempt.status || "-"],
      ["错误类型", attempt.errorType || "-"],
      ["状态码", attempt.statusCode ?? "-"],
      ["耗时", attempt.durationMs === null || attempt.durationMs === undefined ? "-" : `${attempt.durationMs} ms`],
      ["创建时间", localTime(attempt.createdAt)],
    ];

    for (const [label, value] of fields) {
      const group = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value;
      group.append(term, detail);
      meta.append(group);
    }

    item.append(top, meta);
    if (attempt.error) {
      const error = document.createElement("p");
      error.className = "attempt-error";
      error.textContent = attempt.error;
      item.append(error);
    }
    attemptList.append(item);
  }
}

function renderDetail(item, attempts) {
  currentDetailItem = item;
  detailSubtitle.textContent = item.id || "";
  setDetailCopyButtons(item);
  setDetailText("#detailEventType", item.eventType);
  setDetailText("#detailSourceSystem", item.sourceSystem);
  setDetailText("#detailTargetUrl", detailValue(item, "targetUrl", "target_url"));
  setDetailText("#detailMethod", item.method);
  setDetailText("#detailRequestId", detailValue(item, "requestId", "request_id"));
  setDetailText("#detailFailureType", item.failureType);
  setDetailText("#detailAttemptCount", `${item.attemptCount}/${item.maxAttempts}`);
  setDetailText("#detailTimeoutSeconds", detailTimeoutSeconds(item));
  setDetailText("#detailDeliveryRun", item.deliveryRun ?? "-");
  setDetailText("#detailNextAttemptAt", localTime(item.nextAttemptAt));
  setDetailText("#detailLastStatusCode", item.lastStatusCode ?? "-");
  setDetailText("#detailCreatedAt", localTime(item.createdAt));
  setDetailText("#detailUpdatedAt", localTime(item.updatedAt));
  setDetailText("#detailDeliveredAt", localTime(item.deliveredAt));
  setDetailText("#detailLastManualAction", firstPresent(item.lastManualAction, item.last_manual_action));
  setDetailText("#detailLastManualActionAt", localTime(firstPresent(item.lastManualActionAt, item.last_manual_action_at)));
  setDetailText("#detailLastManualActionBy", firstPresent(item.lastManualActionBy, item.last_manual_action_by));
  setDetailText("#detailResolutionNote", firstPresent(item.resolutionNote, item.resolution_note));
  document.querySelector("#detailHeaders").textContent = formatJson(maskHeaders(item.headers));
  document.querySelector("#detailBody").textContent = formatJson(item.body);
  document.querySelector("#detailLastError").textContent = item.lastError || "";

  const status = document.querySelector("#detailStatus");
  status.textContent = statusLabels[item.status] || item.status || "-";
  status.className = `status-pill ${item.status || ""}`;
  configureDeadLetterButton(deadLetterDetailButton, item, { hideWhenUnavailable: false });

  renderAttempts(attempts);
}

async function loadDetail(id, options = {}) {
  detailContent.hidden = true;
  currentDetailItem = null;
  setDetailCopyButtons(null);
  configureDeadLetterButton(deadLetterDetailButton, null, { hideWhenUnavailable: false });
  if (!options.silent) {
    detailLoading.hidden = false;
    detailLoading.textContent = "正在加载...";
  }
  detailSubtitle.textContent = id;

  try {
    const [item, attemptsResult] = await Promise.all([
      fetchJson(`/api/notifications/${id}`),
      fetchJson(`/api/notifications/${id}/attempts`),
    ]);
    if (currentDetailId !== id) return;
    renderDetail(item, attemptsResult.items || []);
    detailLoading.hidden = true;
    detailContent.hidden = false;
  } catch (error) {
    currentDetailItem = null;
    setDetailCopyButtons(null);
    detailLoading.textContent = error.message || "详情加载失败";
    detailLoading.hidden = false;
    showMessage(detailLoading.textContent, "error");
  }
}

async function openDetail(id) {
  currentDetailId = id;
  detailModal.hidden = false;
  document.body.classList.add("detail-open");
  detailPanel.focus();
  await loadDetail(id);
}

function closeDetail() {
  currentDetailId = null;
  currentDetailItem = null;
  detailModal.hidden = true;
  detailContent.hidden = true;
  detailLoading.hidden = true;
  setDetailCopyButtons(null);
  configureDeadLetterButton(deadLetterDetailButton, null, { hideWhenUnavailable: false });
  document.body.classList.remove("detail-open");
}

function mockUrl(path = "") {
  return `${window.location.origin}/mock/vendor/crm${path}`;
}

function setExample(kind) {
  const targetUrl = form.elements.targetUrl;
  const eventType = form.elements.eventType;
  const sourceSystem = form.elements.sourceSystem;
  const requestId = form.elements.requestId;
  const timeoutSeconds = form.elements.timeoutSeconds;
  const headers = form.elements.headers;
  const body = form.elements.body;
  if (timeoutSeconds) timeoutSeconds.value = "";

  if (kind === "failure") {
    targetUrl.value = mockUrl("?fail=1");
    eventType.value = "inventory.changed";
    sourceSystem.value = "order-service";
    requestId.value = timestampId("failure");
    headers.value = JSON.stringify({ "X-Vendor-Token": "demo-token" }, null, 2);
    body.value = JSON.stringify({ sku: "SKU-2026", delta: -1, orderId: "O-90001" }, null, 2);
    return;
  }

  targetUrl.value = mockUrl();
  eventType.value = "subscription.paid";
  sourceSystem.value = "billing-service";
  requestId.value = timestampId("success");
  headers.value = JSON.stringify({ "X-Vendor-Token": "demo-token" }, null, 2);
  body.value = JSON.stringify(
    {
      contactId: "C-10086",
      status: "paid",
      paidAt: "2026-06-08T14:00:00Z",
    },
    null,
    2
  );
}

for (const tab of statusTabs) {
  tab.addEventListener("click", () => {
    for (const item of statusTabs) item.classList.remove("active");
    tab.classList.add("active");
    currentStatus = tab.dataset.status || "";
    resetPagination();
    loadNotifications();
  });
}

filterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  resetPagination();
  loadNotifications();
});
clearFiltersButton.addEventListener("click", () => {
  filterForm.reset();
  filterForm.elements.limit.value = "50";
  resetPagination();
  loadNotifications();
});
exportCsvButton.addEventListener("click", openCsvExport);
form.addEventListener("submit", submitNotification);
apiKeyInput?.addEventListener("input", persistApiKey);
apiKeyInput?.addEventListener("change", persistApiKey);
bodyTextarea.addEventListener("input", () => {
  if (!bodyJsonError.hidden) setBodyJsonError();
});
formatBodyJsonButton.addEventListener("click", formatBodyJson);
refreshButton.addEventListener("click", loadNotifications);
prevPageButton.addEventListener("click", () => {
  const pagination = lastPagination || normalizePagination(null, 0);
  const previousOffset = pagination.previousOffset ?? pagination.offset - pagination.limit;
  setOffsetAndLoad(previousOffset);
});
nextPageButton.addEventListener("click", () => {
  const pagination = lastPagination || normalizePagination(null, 0);
  const nextOffset = pagination.nextOffset ?? pagination.offset + pagination.limit;
  setOffsetAndLoad(nextOffset);
});
refreshHealthButton.addEventListener("click", () => loadHealth({ force: true }));
autoRefreshToggle.addEventListener("click", () => setAutoRefreshPaused(!autoRefreshPaused));
retryFailedBatchButton.addEventListener("click", retryFailedBatch);
fillSuccess.addEventListener("click", () => setExample("success"));
fillFailure.addEventListener("click", () => setExample("failure"));
copyCurlButton.addEventListener("click", copyCurl);
closeDetailButton.addEventListener("click", closeDetail);
refreshDetailButton.addEventListener("click", () => {
  if (currentDetailId) loadDetail(currentDetailId);
});
retryDetailButton.addEventListener("click", () => {
  if (currentDetailId) retryNotification(currentDetailId, { refreshDetail: true });
});
deadLetterDetailButton.addEventListener("click", () => {
  if (currentDetailId) markDeadLetter(currentDetailId, { button: deadLetterDetailButton, refreshDetail: true });
});
copyDetailIdButton.addEventListener("click", () => copyValue("任务 ID", detailValue(currentDetailItem, "id", "notificationId", "notification_id")));
copyDetailTargetUrlButton.addEventListener("click", () => copyValue("目标 URL", detailValue(currentDetailItem, "targetUrl", "target_url")));
copyDetailRequestIdButton.addEventListener("click", () => copyValue("Request ID", detailValue(currentDetailItem, "requestId", "request_id")));
detailModal.addEventListener("click", (event) => {
  if (event.target.matches("[data-close-detail]")) closeDetail();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailModal.hidden) closeDetail();
});

loadApiKey();
setExample("success");
setAutoRefreshPaused(false);
loadNotifications();
refreshTimer = setInterval(tickAutoRefresh, 2500);
healthTimer = setInterval(() => {
  if (!autoRefreshPaused) loadHealth({ silent: true, force: true });
}, 10000);
window.addEventListener("beforeunload", () => {
  clearInterval(refreshTimer);
  clearInterval(healthTimer);
});
