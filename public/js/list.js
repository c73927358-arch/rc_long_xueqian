// Notification list rendering and retry/dead-letter commands.
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
