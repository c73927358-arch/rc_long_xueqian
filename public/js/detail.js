// Detail drawer rendering, attempts rendering, and copy actions.
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
