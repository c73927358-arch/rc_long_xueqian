// Event wiring and application startup.
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
