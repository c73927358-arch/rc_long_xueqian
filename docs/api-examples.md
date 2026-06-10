# API 接入示例

本文面向接入通知服务的内部业务系统，展示广告注册通知、CRM Contact 状态更新和库存变更三个原始场景。

完整字段和状态码契约见 [api-contract.md](api-contract.md)。本文更偏向业务接入样例和调用判断。

示例默认本地通知服务地址为：

```text
http://127.0.0.1:8001
```

如果你用的是 8000 或其他端口，把 curl 中的端口替换为实际端口即可。

## 1. 接入原则

业务系统只调用内部通知服务：

```text
POST /api/notifications
```

业务系统不直接等待外部供应商处理结果。只要通知服务返回创建成功，说明任务已经被内部服务接收并持久化，后续由后台 Worker 异步投递。

当前投递语义是至少一次。建议每个业务事件都传入稳定的 `requestId`，例如 `user-service:user.registered:U-10001` 或 `billing:subscription.paid:S-20260609-001`。重复提交相同 `requestId` 时，服务返回已有任务，避免业务系统重试提交时创建多条内部任务。

### 1.1 可选调用方 API Key

调用方 API Key 默认关闭。服务启动时设置 `NOTIFICATION_API_KEYS` 后，创建通知、单任务重试、标记死信和批量重试这些写接口都需要调用方 Key：

```bash
NOTIFICATION_API_KEYS=dev-caller-key python3 server.py --host 127.0.0.1 --port 8001
```

可用两种 Header 传递 Key：

```text
X-Notification-Api-Key: dev-caller-key
Authorization: Bearer dev-caller-key
```

无 Key 调用写接口会返回 HTTP `401`，不会创建任务：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "auth-demo-missing-key",
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "body": {"contactId": "C-10086", "status": "paid"},
    "maxAttempts": 1
  }'
```

预期响应体：

```json
{
  "error": "unauthorized"
}
```

正确 Key 创建通知：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -H 'X-Notification-Api-Key: dev-caller-key' \
  -d '{
    "requestId": "auth-demo-create-001",
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "method": "PATCH",
    "headers": {
      "Authorization": "Bearer crm-demo-token"
    },
    "body": {
      "contactId": "C-10086",
      "status": "paid"
    },
    "maxAttempts": 1
  }'
```

正确 Key 重新投递失败或死信任务：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications/{id}/retry \
  -H 'Authorization: Bearer dev-caller-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "actionBy": "ops-user@example.com",
    "resolutionNote": "供应商已恢复，人工重新入队"
  }'
```

正确 Key 标记死信：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications/{id}/dead-letter \
  -H 'X-Notification-Api-Key: dev-caller-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "actionBy": "ops-user@example.com",
    "resolutionNote": "供应商接口长期不可用，已转人工确认"
  }'
```

注意不要把真实调用方 Key 放进服务端日志、浏览器截图、工单、`resolutionNote` 或共享文档。这里的调用方 Key 只保护通知服务写入口，不会投递给供应商；供应商自己的认证 Header 仍写在请求体的 `headers` 中。

## 2. 通用请求格式

```json
{
  "requestId": "业务侧幂等 ID",
  "eventType": "业务事件类型",
  "sourceSystem": "来源业务系统",
  "targetUrl": "外部供应商 HTTP(S) API 地址",
  "method": "POST",
  "headers": {
    "X-Vendor-Token": "供应商认证信息"
  },
  "body": {
    "message": "实际投递给供应商的业务数据"
  },
  "maxAttempts": 5,
  "timeoutSeconds": 3
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `requestId` | 业务侧幂等 ID，建议必传。重复提交同一值会返回已有任务 |
| `eventType` | 事件类型，用于排查和过滤 |
| `sourceSystem` | 来源业务系统，用于排查和过滤 |
| `targetUrl` | 外部供应商地址，必须是绝对 HTTP(S) URL，并满足目标地址安全校验 |
| `method` | 投递到供应商时使用的 HTTP Method，支持 `POST`、`PUT`、`PATCH` |
| `headers` | 投递给供应商的 Header。页面和 API 展示会对敏感字段脱敏 |
| `body` | 投递给供应商的 Body。对象和数组会按 JSON 发送 |
| `maxAttempts` | 最大投递次数，当前实现会限制在 1 到 10 之间 |
| `timeoutSeconds` | 可选的单任务供应商 HTTP 请求超时。省略或为 `null` 时使用全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`；传入有限数字时限制在 0.1 到 60.0 秒之间 |

本地调试时，如果未配置外部供应商白名单，可以把 `targetUrl` 换成同源 mock vendor：

```text
http://127.0.0.1:8001/mock/vendor/{vendorName}
```

生产或准生产接入真实供应商时，需要把供应商 Origin 配到 `NOTIFICATION_ALLOWED_TARGETS`。

### 2.1 单任务 timeoutSeconds 示例

多数业务系统不需要传 `timeoutSeconds`，统一使用全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` 更容易运维。适合传单任务覆盖值的场景是：某个供应商 SLA 明确要求快速失败、供应商接口调用成本很高，或某类通知不能长时间占用 Worker。

下面示例把 CRM 通知的供应商 HTTP 请求限制为 2 秒。任务创建成功后，可通过详情接口确认 `timeoutSeconds` 已持久化；如果目标响应超过 2 秒，attempt 会记录 `errorType=timeout`。

```bash
curl -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "billing-service:subscription.paid:S-timeout-demo",
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm?delayMs=3000",
    "method": "PATCH",
    "headers": {
      "Authorization": "Bearer crm-demo-token"
    },
    "body": {
      "contactId": "C-10086",
      "status": "paid"
    },
    "maxAttempts": 1,
    "timeoutSeconds": 2
  }'
```

边界行为：

- `timeoutSeconds` 省略或传 `null`：使用 Worker 投递时当前进程的全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`。
- `0`、负数或小于 `0.1`：按 `0.1` 秒处理。
- 大于 `60.0`：按 `60.0` 秒处理。
- 布尔值、非数字字符串、对象、数组或非有限数字：创建任务返回 HTTP `400`。
- 已经写入任务级值的通知，不受后续全局环境变量调整影响；未写入任务级值的通知会继续使用全局配置。

非法值示例：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "timeoutSeconds": true
  }'
```

预期返回 HTTP `400`，响应体包含类似：

```json
{
  "error": "timeoutSeconds must be a number"
}
```

### 2.2 查询、导出和白屏排障示例

按创建时间范围查看任务：

```bash
curl "http://127.0.0.1:8001/api/notifications?createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&sort=createdAt&order=desc"
```

按更新时间范围查看任务：

```bash
curl "http://127.0.0.1:8001/api/notifications?updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

列表和 CSV 导出使用同一组过滤条件，因此可以把页面当前筛选条件直接复用到导出：

```bash
curl -o notifications.csv "http://127.0.0.1:8001/api/notifications/export.csv?status=failed&createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

非法时间属于调用参数错误，服务应返回 HTTP `400`，业务系统或排障脚本需要修正参数后再查：

```bash
curl -i "http://127.0.0.1:8001/api/notifications?createdFrom=not-a-time"
```

页面白屏或详情弹窗异常时，优先打开 `/health` 和 `/api/stats` 查看 `serviceVersion`、`schemaVersion`。这两个字段用于判断是否出现旧服务和新前端错配；它们不是完整迁移系统，也不能单独证明数据库 schema 已完成升级。

详情抽屉里的 `requestId` 和 `targetUrl` 复制按钮用于把前端现场和 API 排查串起来：复制后对照 `GET /api/notifications/{id}`、`GET /api/notifications/{id}/attempts`、Network 面板请求和供应商日志，可以快速判断是页面渲染问题、请求目标问题，还是后端详情数据缺失。

## 3. vendorKey 演进示例

当前第一版后端不要求实现 `vendorKey`，业务系统仍通过 `targetUrl`、Header 和 Body 显式描述一次供应商 HTTP 请求。这样做的原因是先验证可靠投递链路，减少配置平台和模板 DSL 的前置复杂度。

当供应商数量增加后，可以演进为 `vendorKey` 模式：

```json
{
  "requestId": "billing-service:subscription.paid:S-20260609-001",
  "eventType": "subscription.paid",
  "sourceSystem": "billing-service",
  "vendorKey": "crm-prod",
  "body": {
    "contactId": "C-10086",
    "userId": "U-10001",
    "status": "paid",
    "subscriptionId": "S-20260609-001"
  }
}
```

服务端供应商配置示例：

```json
{
  "crm-prod": {
    "targetUrl": "https://crm.vendor.example/api/contacts/C-10086/status",
    "method": "PATCH",
    "defaultHeaders": {
      "AuthorizationSecretRef": "secret://crm/prod/token",
      "X-Client": "notification-service"
    },
    "timeoutSeconds": 3,
    "maxAttempts": 5,
    "allowedEventTypes": ["subscription.paid"],
    "allowedSourceSystems": ["billing-service"]
  },
  "inventory-prod": {
    "targetUrl": "https://inventory.vendor.example/api/inventory/adjustments",
    "method": "POST",
    "defaultHeaders": {
      "X-Vendor-TokenSecretRef": "secret://inventory/prod/token"
    },
    "timeoutSeconds": 2,
    "maxAttempts": 5,
    "allowedEventTypes": ["order.paid"],
    "allowedSourceSystems": ["order-service"]
  }
}
```

`vendorKey` 的预期收益：

- 业务系统不再重复维护供应商 URL、默认 Header、超时和重试参数。
- 密钥可以由通知服务或密钥系统托管，减少 Header 明文在业务系统之间流转。
- `vendorKey` 可以绑定允许的 `eventType`、`sourceSystem` 和 Origin 白名单，降低误投到错误供应商或错误环境的风险。
- 供应商地址变更时，只改通知服务配置，不需要所有业务系统同步发版。

迁移建议：

- 第一阶段：继续支持 `targetUrl` 透传，同时为高频供应商增加只读 `vendorKey` 配置示例。
- 第二阶段：允许请求同时带 `vendorKey` 和业务 Body，服务端解析出 `targetUrl`、method 和默认 Header。
- 第三阶段：生产关键供应商禁止手写 `targetUrl`，只允许经过审批的 `vendorKey`。
- 第四阶段：按 `vendorKey` 增加供应商级限流、熔断、告警、失败统计和密钥轮换。

## 4. 广告注册通知

场景：用户通过第三方广告系统引流并成功注册后，通知对应广告系统记录转化。

真实供应商 payload 示例：

```json
{
  "requestId": "user-service:user.registered:U-10001",
  "eventType": "user.registered",
  "sourceSystem": "user-service",
  "targetUrl": "https://ads-api.vendor.example/v1/conversions/register",
  "method": "POST",
  "headers": {
    "X-Vendor-Token": "ad-demo-token",
    "X-Trace-Id": "trace-user-register-10001"
  },
  "body": {
    "userId": "U-10001",
    "campaignId": "CMP-20260609",
    "clickId": "CLICK-889900",
    "channel": "third-party-ad",
    "registeredAt": "2026-06-09T10:15:00+08:00"
  },
  "maxAttempts": 5
}
```

本地可运行 curl：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "user-service:user.registered:U-10001",
    "eventType": "user.registered",
    "sourceSystem": "user-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/ad-system",
    "method": "POST",
    "headers": {
      "X-Vendor-Token": "ad-demo-token",
      "X-Trace-Id": "trace-user-register-10001"
    },
    "body": {
      "userId": "U-10001",
      "campaignId": "CMP-20260609",
      "clickId": "CLICK-889900",
      "channel": "third-party-ad",
      "registeredAt": "2026-06-09T10:15:00+08:00"
    },
    "maxAttempts": 5,
    "timeoutSeconds": 3
  }'
```

预期提交响应：

```json
{
  "id": "内部任务 ID",
  "status": "queued",
  "duplicate": false
}
```

## 5. CRM Contact 状态更新

场景：用户订阅付款成功后，通知 CRM 系统把 Contact 状态更新为已付费或活跃。

真实供应商 payload 示例：

```json
{
  "requestId": "billing-service:subscription.paid:S-20260609-001",
  "eventType": "subscription.paid",
  "sourceSystem": "billing-service",
  "targetUrl": "https://crm.vendor.example/api/contacts/C-10086/status",
  "method": "PATCH",
  "headers": {
    "Authorization": "Bearer crm-demo-token",
    "X-Trace-Id": "trace-subscription-paid-001"
  },
  "body": {
    "contactId": "C-10086",
    "userId": "U-10001",
    "status": "paid",
    "subscriptionId": "S-20260609-001",
    "paidAt": "2026-06-09T10:20:00+08:00"
  },
  "maxAttempts": 5
}
```

本地可运行 curl：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "billing-service:subscription.paid:S-20260609-001",
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "method": "PATCH",
    "headers": {
      "Authorization": "Bearer crm-demo-token",
      "X-Trace-Id": "trace-subscription-paid-001"
    },
    "body": {
      "contactId": "C-10086",
      "userId": "U-10001",
      "status": "paid",
      "subscriptionId": "S-20260609-001",
      "paidAt": "2026-06-09T10:20:00+08:00"
    },
    "maxAttempts": 5
  }'
```

## 6. 库存变更通知

场景：用户购买商品后，通知库存系统扣减库存。

真实供应商 payload 示例：

```json
{
  "requestId": "order-service:order.paid:O-90001",
  "eventType": "order.paid",
  "sourceSystem": "order-service",
  "targetUrl": "https://inventory.vendor.example/api/inventory/adjustments",
  "method": "POST",
  "headers": {
    "X-Vendor-Token": "inventory-demo-token",
    "X-Trace-Id": "trace-order-paid-90001"
  },
  "body": {
    "orderId": "O-90001",
    "items": [
      {
        "sku": "SKU-2026",
        "delta": -1,
        "reason": "order_paid"
      }
    ],
    "occurredAt": "2026-06-09T10:25:00+08:00"
  },
  "maxAttempts": 5
}
```

本地可运行 curl：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "order-service:order.paid:O-90001",
    "eventType": "order.paid",
    "sourceSystem": "order-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/inventory",
    "method": "POST",
    "headers": {
      "X-Vendor-Token": "inventory-demo-token",
      "X-Trace-Id": "trace-order-paid-90001"
    },
    "body": {
      "orderId": "O-90001",
      "items": [
        {
          "sku": "SKU-2026",
          "delta": -1,
          "reason": "order_paid"
        }
      ],
      "occurredAt": "2026-06-09T10:25:00+08:00"
    },
    "maxAttempts": 5
  }'
```

## 7. 提交失败和投递失败的区别

### 7.1 业务系统提交失败

提交失败发生在业务系统调用 `POST /api/notifications` 时。此时任务尚未被成功创建，业务系统需要根据响应处理。

| HTTP 状态码 | 含义 | 业务系统处理建议 |
| --- | --- | --- |
| `201` | 新任务已创建并持久化 | 主流程可以继续，后续通过任务状态或管理页查看投递结果 |
| `200` + `duplicate=true` | 同一 `requestId` 的任务已存在 | 视为提交成功，不要再创建新的业务事件 |
| `400` | 请求格式或参数错误，例如 JSON 非法、URL 非法、目标不在白名单、Header 不是对象、Method 不支持 | 修正请求后再提交，不要盲目自动重试 |
| `401` | 服务已设置 `NOTIFICATION_API_KEYS`，但写接口缺少或提供了错误调用方 Key | 补上 `X-Notification-Api-Key` 或 `Authorization: Bearer ...` 后重试；不要把真实 Key 粘贴到日志、截图或工单 |
| `404` | 接口路径不存在，或查询/重试的任务 ID 不存在 | 检查 URL、任务 ID 或调用流程 |
| `409` | 对正在 `delivering` 的任务执行手动重试 | 等待投递结束，或等待租约回收/人工处理 |
| `500` | 服务内部异常，例如数据库不可用 | 业务系统可按内部重试策略稍后重试，并通知运维检查 `/health` |

判断口径：只有 `POST /api/notifications` 返回 `201`，或返回 `200` 且 `duplicate=true`，才表示通知服务已经接收该业务事件。

### 7.2 外部供应商投递失败

投递失败发生在通知任务已经创建之后，由后台 Worker 调用供应商 API 时产生。此时业务系统的提交接口通常已经成功返回。

常见投递失败：

| 失败类型 | 示例 | 系统处理 |
| --- | --- | --- |
| `http_error` | 供应商返回 429、500、503 | 记录状态码，未达到最大次数时进入 `waiting_retry` |
| `timeout` | 供应商长时间不响应 | 记录 timeout，按重试策略处理 |
| `network_error` | DNS、连接失败或网络不可达 | 记录网络错误，按重试策略处理 |
| `delivery_error` | 投递过程中的未知异常 | 记录兜底错误，避免任务无声丢失 |

处理建议：

- 业务系统无需阻塞主流程等待供应商投递成功。
- 支持人员通过页面、`GET /api/notifications` 或 `GET /api/notifications/{id}/attempts` 排查失败原因。
- 任务进入 `failed` 后，可以通过单任务重试或批量重试重新入队。
- 下游供应商必须基于业务 ID、订单号、用户 ID 或通知 `requestId` 做幂等，因为至少一次语义可能产生重复 HTTP 请求。

## 8. 安全和运行配置

### 8.1 目标 URL 白名单

生产环境建议配置精确 Origin 白名单：

```bash
NOTIFICATION_ALLOWED_TARGETS=https://ads-api.vendor.example,https://crm.vendor.example,https://inventory.vendor.example \
  python3 server.py --host 127.0.0.1 --port 8001
```

说明：

- 白名单按 Origin 匹配，即 scheme、host、port 必须一致。
- 白名单值不能包含 path、query、username 或 password。
- 配置白名单后，不在白名单中的外部地址会在提交阶段返回 400，不会入队。
- 即使未配置白名单，服务也会拒绝 localhost、私网、link-local、metadata 类地址；本地 mock vendor 例外见下一节。

### 8.2 本地 mock vendor 例外

为了白屏调试和本地验收，服务默认允许当前页面同源的 `/mock/vendor/*`：

```text
http://127.0.0.1:8001/mock/vendor/crm
```

如需关闭本地 mock 例外：

```bash
ALLOW_LOCAL_MOCK_VENDOR=false python3 server.py --host 127.0.0.1 --port 8001
```

关闭后，localhost 或 127.0.0.1 目标会按 SSRF 防护被拒绝。

### 8.3 投递超时

通过环境变量配置默认的供应商 HTTP 请求超时时间：

```bash
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=3 python3 server.py --host 127.0.0.1 --port 8001
```

当前实现口径：

- 默认值为 `8.0` 秒。
- 小数可用，例如 `0.2`。
- 过小值会 clamp 到 `0.1` 秒。
- 过大值会 clamp 到 `60.0` 秒。
- 非法值回退到默认值。
- 创建通知时可以通过 `timeoutSeconds` 覆盖单个任务的投递超时；省略或传 `null` 时使用上述全局配置。
- 单任务 `timeoutSeconds` 的合法数值同样 clamp 到 `0.1` 到 `60.0` 秒；非法类型会让创建接口返回 HTTP `400`，不会创建任务。
- 全局配置适合运维统一调整默认行为，任务级覆盖值适合供应商或事件类型有明确差异的少数场景。

### 8.4 delivering 租约回收

当前实现会处理两类 `delivering` 卡住场景：

- 服务启动时，把重启前遗留的 `delivering` 任务回收到 `queued`，并写入类似 `service restarted while delivery was in progress` 的 `lastError`。
- Worker 运行中，按租约扫描超过阈值的 `delivering` 任务，记录 `lease_timeout` attempt，并根据剩余尝试次数重新入队或标记最终失败。

租约可通过以下环境变量配置：

```bash
NOTIFICATION_DELIVERING_LEASE_SECONDS=120
```

当前实现口径：

- 默认值为 `60.0` 秒。
- 小数可用，例如 `0.5`，方便自动化测试。
- 过小值会 clamp 到 `0.1` 秒。
- 过大值会 clamp 到 `3600.0` 秒。
- 非法值回退到默认值。
- `/health` 会返回 `worker.deliveringLeaseSeconds`、`worker.lastLeaseRecoveryCount` 和 `queue.expiredDeliveringCount`。
- 回收时不会清空 attempts 历史，并在 `lastError` 中标明 lease expired。
- 租约时间应大于正常投递超时，避免一个正常慢请求被另一个 Worker 过早重复领取。

生产版如果担心唯一 Worker 线程永久卡在系统调用里，可以把租约扫描拆成独立 watchdog 或使用多 Worker，避免回收能力也被同一个线程阻塞。

### 8.5 Worker 并发配置

后台投递线程数可通过环境变量配置：

```bash
NOTIFICATION_WORKER_CONCURRENCY=2
```

当前文档约定：

- 默认值为 `1`，表示单 Worker 线程，适合本地 Demo、SQLite 文件数据库和可重复排障。
- 建议范围为 `1` 到 `4`；如果后端实现设置硬上限，非法值应回退、clamp 或在启动时给出明确错误。
- 调大前先观察 `/health.queue.readyCount` 是否长期大于 0，以及供应商是否允许更高并发。
- 调大后继续观察 `/health.worker.concurrency`、`worker.threadCount`、`worker.aliveCount`、`worker.lastError` 和 `queue.readyCount`。
- SQLite + `DB_LOCK` 的第一版只能提高慢 HTTP 等待时的并行度，不能让数据库领取和状态更新无限并行。
- 风险包括供应商 429/5xx 增加、SQLite 锁等待变多、HTTP 超时放大、重复投递窗口变宽，以及日志和告警噪声上升。

如果 `readyCount` 在并发调大后仍持续增长，优先排查数据库锁、供应商限流、网络超时和单机资源，而不是继续盲目加线程。生产流量继续上升时，应迁移到 PostgreSQL 行锁、多进程 Worker、消息队列或调度系统。
