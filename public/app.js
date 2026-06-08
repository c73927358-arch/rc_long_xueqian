const form = document.querySelector("#notificationForm");
const list = document.querySelector("#notificationList");
const template = document.querySelector("#notificationTemplate");
const message = document.querySelector("#message");
const refreshButton = document.querySelector("#refreshButton");
const statusTabs = document.querySelectorAll(".status-tabs button");
const fillSuccess = document.querySelector("#fillSuccess");
const fillFailure = document.querySelector("#fillFailure");

let currentStatus = "";
let refreshTimer = null;

const statusLabels = {
  queued: "等待投递",
  delivering: "投递中",
  waiting_retry: "等待重试",
  succeeded: "投递成功",
  failed: "最终失败",
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

function localTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

async function submitNotification(event) {
  event.preventDefault();
  const data = new FormData(form);
  let payload;
  try {
    payload = {
      eventType: data.get("eventType"),
      sourceSystem: data.get("sourceSystem"),
      targetUrl: data.get("targetUrl"),
      method: data.get("method"),
      maxAttempts: Number(data.get("maxAttempts") || 5),
      headers: parseJsonField(data.get("headers") || "", {}),
      body: parseJsonField(data.get("body") || "", {}),
    };
  } catch (error) {
    showMessage(`JSON 格式错误：${error.message}`, "error");
    return;
  }

  const response = await fetch("/api/notifications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    showMessage(result.error || "提交失败", "error");
    return;
  }
  showMessage(`已入队：${result.id}`);
  await loadNotifications();
}

async function loadNotifications() {
  const query = new URLSearchParams();
  if (currentStatus) query.set("status", currentStatus);
  const response = await fetch(`/api/notifications?${query.toString()}`);
  const result = await response.json();
  if (!response.ok) {
    showMessage(result.error || "加载失败", "error");
    return;
  }
  renderList(result.items || []);
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

    node.querySelector(".retry-button").addEventListener("click", () => retryNotification(item.id));
    node.querySelector(".copy-button").addEventListener("click", async () => {
      await navigator.clipboard.writeText(item.id);
      showMessage("任务 ID 已复制");
    });

    card.dataset.id = item.id;
    list.append(node);
  }
}

async function retryNotification(id) {
  const response = await fetch(`/api/notifications/${id}/retry`, { method: "POST" });
  const result = await response.json();
  if (!response.ok) {
    showMessage(result.error || "重试失败", "error");
    return;
  }
  showMessage(`已重新入队：${result.id}`);
  await loadNotifications();
}

function mockUrl(path = "") {
  return `${window.location.origin}/mock/vendor/crm${path}`;
}

function setExample(kind) {
  const targetUrl = form.elements.targetUrl;
  const eventType = form.elements.eventType;
  const sourceSystem = form.elements.sourceSystem;
  const headers = form.elements.headers;
  const body = form.elements.body;

  if (kind === "failure") {
    targetUrl.value = mockUrl("?fail=1");
    eventType.value = "inventory.changed";
    sourceSystem.value = "order-service";
    headers.value = JSON.stringify({ "X-Vendor-Token": "demo-token" }, null, 2);
    body.value = JSON.stringify({ sku: "SKU-2026", delta: -1, orderId: "O-90001" }, null, 2);
    return;
  }

  targetUrl.value = mockUrl();
  eventType.value = "subscription.paid";
  sourceSystem.value = "billing-service";
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
    loadNotifications();
  });
}

form.addEventListener("submit", submitNotification);
refreshButton.addEventListener("click", loadNotifications);
fillSuccess.addEventListener("click", () => setExample("success"));
fillFailure.addEventListener("click", () => setExample("failure"));

setExample("success");
loadNotifications();
refreshTimer = setInterval(loadNotifications, 2500);
window.addEventListener("beforeunload", () => clearInterval(refreshTimer));
