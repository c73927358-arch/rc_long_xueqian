// Success and failure example form fillers.
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
