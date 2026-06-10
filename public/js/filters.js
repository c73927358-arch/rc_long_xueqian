// Filter parsing, pagination, CSV export, and query construction.
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
