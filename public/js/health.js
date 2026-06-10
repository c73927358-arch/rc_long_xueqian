// Health and statistics read models plus renderers.
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
