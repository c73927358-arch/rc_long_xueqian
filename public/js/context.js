// DOM references, mutable page state, and display labels.
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
