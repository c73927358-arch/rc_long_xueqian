# API 契约

本文描述当前 HTTP 通知投递服务的第一版 API 契约。所有接口默认返回 JSON，示例 Base URL 为：

```text
http://127.0.0.1:8001
```

## OpenAPI 风格摘要

本文不是完整 OpenAPI YAML，而是面向业务系统接入的结构化契约摘要。当前 Demo 支持可选的最小调用方 API Key 鉴权：默认关闭；设置 `NOTIFICATION_API_KEYS` 后，写接口需要 `X-Notification-Api-Key` 或 `Authorization: Bearer ...`。这不是完整用户身份、RBAC 或审计体系，生产化时仍应在网关或服务层补充调用方认证、权限、密钥轮换和审计。

通用约定：

| 项 | 约定 |
| --- | --- |
| Base URL | 本地示例为 `http://127.0.0.1:8001`，生产按部署环境替换 |
| Content-Type | JSON 接口请求和响应使用 `application/json; charset=utf-8` |
| 时间格式 | 响应时间使用 ISO 8601 / RFC 3339 字符串，示例为 `2026-06-09T10:20:00Z` |
| 幂等键 | `POST /api/notifications` 通过 `requestId` 做业务幂等 |
| 调用方鉴权 | `NOTIFICATION_API_KEYS` 未设置时关闭；设置后写接口要求 `X-Notification-Api-Key` 或 `Authorization: Bearer ...` |
| 错误响应 | 普通错误返回 ErrorResponse 对象，格式见 `1.5 错误响应对象` |

接口目录：

| Method | Path | operationId | 用途 | 主要成功响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/notifications` | `createNotification` | 创建通知任务，后台异步投递供应商 | `201` 新建，`200` 幂等命中 |
| `GET` | `/api/notifications` | `listNotifications` | 查询任务列表，支持过滤、分页和排序 | `200` `{ items, pagination }` |
| `GET` | `/api/notifications/export.csv` | `exportNotificationsCsv` | 按列表条件导出排障 CSV | `200` `text/csv` |
| `GET` | `/api/notifications/{id}` | `getNotification` | 查询单个任务详情 | `200` Notification |
| `GET` | `/api/notifications/{id}/attempts` | `listNotificationAttempts` | 查询单个任务的投递尝试历史 | `200` `{ items }` |
| `POST` | `/api/notifications/{id}/retry` | `retryNotification` | 人工重新入队单个任务 | `200` Notification |
| `POST` | `/api/notifications/{id}/dead-letter` | `markNotificationDeadLetter` | 把任务标记为死信/人工接管 | `200` Notification |
| `POST` | `/api/notifications/retry` | `retryNotificationsBatch` | 批量重新入队失败任务 | `200` `{ count, items }` |
| `GET` | `/health` | `getHealth` | 健康检查和 Worker/队列状态 | `200` HealthPayload |
| `GET` | `/api/stats` | `getStats` | 聚合统计和趋势看板数据 | `200` StatsPayload |

## 1. 通用约定

### 1.1 状态枚举

通知任务 `status`：

| 值 | 含义 |
| --- | --- |
| `queued` | 已接收，等待 Worker 投递 |
| `delivering` | Worker 正在发送供应商 HTTP 请求 |
| `waiting_retry` | 本次失败，等待下次自动重试 |
| `succeeded` | 供应商返回 2xx，HTTP 层投递成功 |
| `failed` | 达到最大尝试次数后仍失败 |
| `dead_letter` | 已由人工标记为死信，不再被 Worker 自动投递，也不会被普通批量重试选中 |

失败类型 `failureType` / `errorType`：

| 值 | 含义 |
| --- | --- |
| `http_error` | 供应商返回非 2xx |
| `timeout` | 请求供应商超时 |
| `network_error` | DNS、连接或网络层失败 |
| `delivery_error` | 投递过程中的兜底异常 |
| `lease_timeout` | `delivering` 租约过期后被回收，属于系统恢复记录，不代表一次真实 HTTP 投递 |

### 1.2 Notification 对象

`GET /api/notifications/{id}` 返回完整任务对象；列表接口返回同一结构的精简预览，通常把 `body` 换成 `bodyPreview`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 通知任务 ID |
| `requestId` | string/null | 业务侧幂等 ID |
| `eventType` | string/null | 业务事件类型 |
| `sourceSystem` | string/null | 来源系统 |
| `targetUrl` | string | 供应商目标地址 |
| `method` | string | 投递方法，`POST`、`PUT` 或 `PATCH` |
| `headers` | object | 展示给前端的 Header，敏感值已脱敏 |
| `body` | any/string | 详情接口返回，敏感字段已脱敏 |
| `bodyPreview` | string | 列表接口返回，便于快速查看 |
| `status` | string | 任务状态枚举 |
| `attemptCount` | number | 当前投递轮次已尝试次数 |
| `deliveryRun` | number | 投递轮次，手动或批量重试后递增 |
| `maxAttempts` | number | 当前轮次最大尝试次数 |
| `timeoutSeconds` | number/null | 任务级供应商 HTTP 请求超时。为 `null` 时使用全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` |
| `nextAttemptAt` | string/null | 下次可被 Worker 领取的时间 |
| `lastError` | string/null | 最近错误，敏感信息已脱敏 |
| `failureType` | string/null | 最近任务级失败分类 |
| `lastStatusCode` | number/null | 最近供应商 HTTP 状态码 |
| `lastManualAction` | string/null | 最近一次人工动作，例如 `dead_letter`、`retry` |
| `lastManualActionAt` | string/null | 最近一次人工动作时间 |
| `lastManualActionBy` | string/null | 最近一次人工动作操作者标识 |
| `resolutionNote` | string/null | 人工处理备注或处置说明，供交接和排障使用 |
| `createdAt` | string | 创建时间 |
| `updatedAt` | string | 最近更新时间 |
| `deliveredAt` | string/null | 成功投递时间 |

### 1.3 Attempt 对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | number | attempt 记录 ID |
| `notificationId` | string | 所属通知任务 ID |
| `attemptNumber` | number | 当前 `deliveryRun` 内的尝试编号 |
| `attemptSequence` | number | 全历史递增尝试编号，不因重试轮次重置 |
| `deliveryRun` | number | 投递轮次 |
| `status` | string | `succeeded` 或 `failed` |
| `statusCode` | number/null | 供应商 HTTP 状态码 |
| `error` | string/null | 本次尝试错误，敏感信息已脱敏 |
| `errorType` | string/null | 本次尝试失败分类 |
| `durationMs` | number | 本次尝试耗时 |
| `createdAt` | string | 记录时间 |

### 1.4 时间范围 Query 参数

`GET /api/notifications` 和 `GET /api/notifications/export.csv` 共享创建时间、更新时间范围过滤参数：

| 参数 | 字段 | 说明 |
| --- | --- | --- |
| `createdFrom` | `createdAt` | 只返回创建时间大于等于该值的任务 |
| `createdTo` | `createdAt` | 只返回创建时间小于等于该值的任务 |
| `updatedFrom` | `updatedAt` | 只返回最近更新时间大于等于该值的任务 |
| `updatedTo` | `updatedAt` | 只返回最近更新时间小于等于该值的任务 |

时间值应使用可解析的 ISO 8601 / RFC 3339 时间戳，推荐带时区，例如 `2026-06-09T00:00:00Z`。范围端点为闭区间；同时传入多个范围参数时与其他过滤条件一起按 AND 关系生效。非法时间、无法解析的时间，或同一字段出现 `From` 晚于 `To` 的范围，返回 HTTP `400`，响应体应包含可定位的错误信息。

### 1.5 错误响应对象

普通 API 错误响应使用统一的轻量格式：

```json
{
  "error": "timeoutSeconds must be a number"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `error` | string | 面向调用方或排障人员的错误摘要，通常可直接定位非法参数、状态冲突或资源不存在 |

常见错误状态：

| 状态码 | 典型场景 |
| --- | --- |
| `400` | 请求体不是 JSON 对象、JSON 无法解析、字段类型非法、目标 URL 或查询参数非法 |
| `401` | 已启用调用方 API Key 鉴权，但写接口缺少 Key、Key 错误，或 `Authorization` 不是有效 Bearer 形式 |
| `404` | 路径不存在，或指定通知任务不存在 |
| `409` | 当前任务状态不允许执行该人工动作，例如正在 `delivering` |
| `500` | 数据库或服务内部异常 |

`/health` 的异常响应会额外携带 `status=degraded`、`serviceVersion`、`schemaVersion`、`database` 和 `now`，便于页面白屏或错配排查；它仍会包含 `error` 字段。

### 1.6 调用方 API Key 鉴权

`NOTIFICATION_API_KEYS` 用于保护调用方对通知服务的写操作。默认未设置时鉴权关闭，保持本地 Demo 和 smoke test 的低门槛；设置后，写接口必须提供匹配的 Key。

配置约定：

| 项 | 说明 |
| --- | --- |
| 环境变量 | `NOTIFICATION_API_KEYS` |
| 默认值 | 未设置或为空时关闭调用方 API Key 鉴权 |
| 多 Key | 建议用逗号分隔，例如 `dev-caller-key,ops-caller-key`，用于不同内部调用方或临时迁移 |
| 请求 Header | `X-Notification-Api-Key: <key>` 或 `Authorization: Bearer <key>` |
| 失败响应 | HTTP `401`，响应体为 `{"error":"unauthorized"}` |

受保护接口：

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/api/notifications` | 创建通知任务 |
| `POST` | `/api/notifications/{id}/retry` | 单任务人工重试 |
| `POST` | `/api/notifications/{id}/dead-letter` | 标记死信/人工处理 |
| `POST` | `/api/notifications/retry` | 批量重试失败任务 |

不受该调用方 Key 保护的接口：

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/notifications` | 查询列表，第一版保留为调试视图 |
| `GET` | `/api/notifications/export.csv` | 导出排障 CSV |
| `GET` | `/api/notifications/{id}` | 查询详情 |
| `GET` | `/api/notifications/{id}/attempts` | 查询 attempts |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/stats` | 聚合统计 |
| `GET` | `/`、`/app.js`、`/styles.css` | 本地页面和静态资源 |
| 任意 | `/mock/vendor/*` | 本地供应商模拟接口，用于调试 Worker 投递 |

鉴权失败时，服务不得创建新通知、不得重新入队、不得标记死信，也不得把提交的 Key 写入任务、attempts、日志、页面错误、截图或工单说明。`Authorization` 作为调用方 Key 的 Header 与 payload 中投递给供应商的 `headers.Authorization` 是两层不同含义：前者只保护通知服务写入口，后者由 Worker 原样投递给供应商。

设计边界：

- 第一版只判断“调用方是否持有共享 Key”，不识别具体用户身份。
- 不做 RBAC、多租户隔离、按来源系统授权、按供应商授权或细粒度操作权限。
- 不做密钥轮换审计、Key 使用统计、Key 归属管理或泄露检测。
- 不替代供应商侧鉴权；供应商 API Key、OAuth、HMAC、mTLS 等仍由 `headers` 透传或未来 `vendorKey` 配置管理。
- 后续可演进为网关 JWT/mTLS、按调用方绑定 `sourceSystem`/`vendorKey`、密钥托管与轮换、操作审计和只读接口权限控制。

### 1.7 任务级投递超时

`timeoutSeconds` 是单个通知任务投递到供应商时的 HTTP 请求超时，单位为秒。它只影响 Worker 对目标 `targetUrl` 的一次 HTTP 调用，不改变业务系统提交通知时的 HTTP 超时，也不改变自动重试次数。

取值规则：

| 场景 | 行为 |
| --- | --- |
| 字段省略或为 `null` | 不在任务上写入覆盖值，Worker 投递时使用当前进程的 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` |
| 有限数字 | clamp 到 `0.1` 到 `60.0` 秒之间，并持久化到任务 |
| `0`、负数或小于 `0.1` | clamp 到 `0.1` 秒 |
| 大于 `60.0` | clamp 到 `60.0` 秒 |
| 布尔值、非数字字符串、对象、数组或非有限数字 | 创建任务返回 HTTP `400`，错误信息说明 `timeoutSeconds` 非法 |

使用建议：

- 大多数业务方省略该字段，统一使用全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`，便于运维统一调节。
- 只有当某个供应商 SLA 明显更短、接口调用成本很高，或某类通知不能长时间占用 Worker 时，才传任务级 `timeoutSeconds`。
- 指定任务级值后，该值随任务持久化；后续修改全局环境变量不会影响已经写入覆盖值的任务。
- `NOTIFICATION_DELIVERING_LEASE_SECONDS` 应大于正常投递超时，避免正在执行的慢请求被租约回收逻辑过早判定为卡住。

## 2. 创建通知

```text
POST /api/notifications
```

请求体：

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `requestId` | 否 | string | 业务侧幂等 ID。非空且重复时返回已有任务 |
| `eventType` | 否 | string | 事件类型，用于过滤和排障 |
| `sourceSystem` | 否 | string | 来源系统 |
| `targetUrl` | 是 | string | 绝对 HTTP(S) URL，必须通过白名单和 SSRF 校验 |
| `method` | 否 | string | 默认 `POST`，支持 `POST`、`PUT`、`PATCH` |
| `headers` | 否 | object | 供应商 Header |
| `body` | 否 | object/array/string/number/boolean/null | 投递 Body |
| `maxAttempts` | 否 | number | 最大投递次数，当前 clamp 到 1 到 10 |
| `timeoutSeconds` | 否 | number/null | 单任务投递超时。省略或 `null` 使用全局配置；有限数字 clamp 到 `0.1` 到 `60.0`；非法类型返回 `400` |

请求示例：

```json
{
  "requestId": "billing:subscription.paid:S-001",
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
  "maxAttempts": 5,
  "timeoutSeconds": 3
}
```

成功创建响应：HTTP `201`。

```json
{
  "id": "notification-id",
  "requestId": "billing:subscription.paid:S-001",
  "status": "queued",
  "duplicate": false,
  "duplicated": false,
  "idempotent": false,
  "idempotency": "created"
}
```

幂等命中响应：HTTP `200`。

```json
{
  "id": "existing-notification-id",
  "requestId": "billing:subscription.paid:S-001",
  "status": "succeeded",
  "duplicate": true,
  "duplicated": true,
  "idempotent": true,
  "idempotency": "reused_existing"
}
```

状态码：

| 状态码 | 含义 |
| --- | --- |
| `201` | 新任务已创建并持久化 |
| `200` | 同一 `requestId` 已存在，返回已有任务 |
| `400` | JSON、URL、Header、Method、`maxAttempts`、`timeoutSeconds` 或目标安全校验失败 |
| `401` | 已启用 `NOTIFICATION_API_KEYS`，但未提供正确调用方 API Key |
| `500` | 服务内部异常 |

## 3. 查询通知列表

```text
GET /api/notifications
```

Query 参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 按任务状态精确过滤 |
| `eventType` | string | 按事件类型关键词过滤 |
| `sourceSystem` | string | 按来源系统关键词过滤 |
| `targetUrl` | string | 按目标 URL 关键词过滤 |
| `createdFrom` | string | 按 `createdAt` 起始时间过滤，闭区间 |
| `createdTo` | string | 按 `createdAt` 截止时间过滤，闭区间 |
| `updatedFrom` | string | 按 `updatedAt` 起始时间过滤，闭区间 |
| `updatedTo` | string | 按 `updatedAt` 截止时间过滤，闭区间 |
| `limit` | number | 返回数量，当前 clamp 到 1 到 200 |
| `offset` | number | 跳过数量，默认 `0`，用于页面分页 |
| `sort` | string | 排序字段，默认 `createdAt`；允许 `createdAt`、`updatedAt`、`nextAttemptAt`、`status` |
| `order` | string | 排序方向，默认 `desc`；允许 `asc` 或 `desc` |

响应：HTTP `200`。

```json
{
  "items": [
    {
      "id": "notification-id",
      "requestId": "billing:subscription.paid:S-001",
      "eventType": "subscription.paid",
      "sourceSystem": "billing-service",
      "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
      "method": "PATCH",
      "headers": {"Authorization": "******"},
      "status": "succeeded",
      "attemptCount": 1,
      "deliveryRun": 1,
      "maxAttempts": 5,
      "timeoutSeconds": 3,
      "nextAttemptAt": null,
      "lastError": null,
      "failureType": null,
      "lastStatusCode": 200,
      "lastManualAction": null,
      "lastManualActionAt": null,
      "lastManualActionBy": null,
      "resolutionNote": null,
      "createdAt": "2026-06-09T10:20:00+08:00",
      "updatedAt": "2026-06-09T10:20:01+08:00",
      "deliveredAt": "2026-06-09T10:20:01+08:00",
      "bodyPreview": "{\"contactId\":\"C-10086\"}"
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "count": 50,
    "hasMore": true,
    "sort": "createdAt",
    "order": "desc"
  }
}
```

分页语义：

- `limit` 控制本页最多返回多少条，服务端仍会做最大值限制，防止一次查询拖垮页面。
- `offset` 从过滤后的结果集中跳过指定条数，`offset=0` 表示第一页。
- `count` 是本次实际返回条数。
- `hasMore=true` 表示还有下一页；下一页可使用 `offset + limit` 请求。
- 排序先按 `sort/order` 指定字段执行；当排序字段值相同时，应再按 `id` 或创建时间做稳定兜底，避免翻页时顺序随机变化。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 查询成功，空结果返回 `items=[]` |
| `400` | `limit`、`offset` 不是数字，`sort/order` 不在允许范围内，或时间范围参数非法 |
| `500` | 服务内部异常 |

### 3.1 导出通知 CSV

```text
GET /api/notifications/export.csv
```

导出接口用于把当前列表筛选结果下载为 CSV，便于人工排查和交接。过滤、分页和排序参数与 `GET /api/notifications` 一致：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 按任务状态精确过滤 |
| `eventType` | string | 按事件类型关键词过滤 |
| `sourceSystem` | string | 按来源系统关键词过滤 |
| `targetUrl` | string | 按目标 URL 关键词过滤 |
| `createdFrom` | string | 按 `createdAt` 起始时间过滤，闭区间 |
| `createdTo` | string | 按 `createdAt` 截止时间过滤，闭区间 |
| `updatedFrom` | string | 按 `updatedAt` 起始时间过滤，闭区间 |
| `updatedTo` | string | 按 `updatedAt` 截止时间过滤，闭区间 |
| `limit` | number | 导出数量上限，仍受服务端最大值限制 |
| `offset` | number | 从过滤后的结果集中跳过指定条数 |
| `sort` | string | 与列表接口相同的排序字段 |
| `order` | string | `asc` 或 `desc` |

响应：HTTP `200`，`Content-Type` 为 `text/csv; charset=utf-8`，建议带 `Content-Disposition: attachment; filename="notifications.csv"`。

CSV 字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 通知任务 ID |
| `requestId` | 业务侧幂等 ID |
| `eventType` | 业务事件类型 |
| `sourceSystem` | 来源系统 |
| `targetUrl` | 供应商目标地址，遵循列表展示的脱敏策略 |
| `status` | 当前任务状态 |
| `attemptCount` | 当前投递轮次已尝试次数 |
| `deliveryRun` | 投递轮次 |
| `lastError` | 最近错误，敏感信息已脱敏 |
| `failureType` | 最近任务级失败分类 |
| `lastStatusCode` | 最近供应商 HTTP 状态码 |
| `createdAt` | 创建时间 |
| `updatedAt` | 最近更新时间 |
| `deliveredAt` | 成功投递时间 |

脱敏边界：

- CSV 不导出 `headers` 字段。
- CSV 不导出完整 `body` 原文或 `bodyPreview`，只导出非敏感排障字段。
- `lastError` 必须复用页面/API 展示的脱敏逻辑，不能泄露 token、authorization、secret、password、key 等敏感值。
- `targetUrl` 是排障字段；生产接入应避免把凭证放进 URL query。如果出现已知敏感 query key，导出时也应展示为脱敏值。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 导出成功；空结果返回只有表头的 CSV |
| `400` | `limit`、`offset`、`sort`、`order` 或时间范围参数不合法 |
| `500` | 服务内部异常 |

## 4. 查询通知详情

```text
GET /api/notifications/{id}
```

响应：HTTP `200`，返回 Notification 对象，包含脱敏后的 `body`。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 查询成功 |
| `404` | 任务不存在 |
| `500` | 服务内部异常 |

## 5. 查询投递尝试

```text
GET /api/notifications/{id}/attempts
```

响应：HTTP `200`。

```json
{
  "items": [
    {
      "id": 1,
      "notificationId": "notification-id",
      "attemptNumber": 1,
      "attemptSequence": 1,
      "deliveryRun": 1,
      "status": "failed",
      "statusCode": 500,
      "error": "target returned HTTP 500",
      "errorType": "http_error",
      "durationMs": 12,
      "createdAt": "2026-06-09T10:20:01+08:00"
    }
  ]
}
```

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 查询成功 |
| `404` | 任务不存在 |
| `500` | 服务内部异常 |

## 6. 单任务重试

```text
POST /api/notifications/{id}/retry
```

请求体可为空；如需记录人工动作，可以传入操作者和备注。

```json
{
  "actionBy": "ops-user@example.com",
  "resolutionNote": "供应商已恢复，人工重新入队"
}
```

兼容说明：后端同时接受 `handledBy`/`note` 和 `actionBy`/`resolutionNote`。前者更贴近前端实现，后者更贴近人工动作语义；响应统一返回 `lastManualActionBy` 和 `resolutionNote`。

响应：HTTP `200`，返回更新后的 Notification 对象。任务会进入 `queued`，`attemptCount` 重置为 0，`deliveryRun` 加 1，历史 attempts 不删除。单条 retry 可以显式重新入队 `failed` 或 `dead_letter` 任务；这是从死信恢复的人工动作，不属于自动重试。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 已重新入队 |
| `401` | 已启用 `NOTIFICATION_API_KEYS`，但未提供正确调用方 API Key |
| `404` | 任务不存在 |
| `409` | 任务正在 `delivering`，或当前状态不允许人工重试 |
| `500` | 服务内部异常 |

## 7. 标记死信 / 人工处理

```text
POST /api/notifications/{id}/dead-letter
```

该接口用于把最终失败且需要人工接管的通知标记为 `dead_letter`。进入死信后，Worker 不再自动投递该任务，普通批量重试也不会选中它；只有单条 `POST /api/notifications/{id}/retry` 才能把它重新入队。

请求体：

```json
{
  "actionBy": "ops-user@example.com",
  "resolutionNote": "供应商接口已下线，已转人工确认，不再自动重试"
}
```

字段说明：

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `actionBy` / `handledBy` | 否 | string | 执行人工动作的操作者标识，例如内部账号、值班人或系统名 |
| `resolutionNote` / `note` | 否 | string | 人工处置说明；不应填写密码、token、客户隐私等敏感信息 |

响应：HTTP `200`，返回更新后的 Notification 对象。

响应关键字段示例：

```json
{
  "id": "notification-id",
  "status": "dead_letter",
  "lastManualAction": "dead_letter",
  "lastManualActionAt": "2026-06-10T09:30:00+08:00",
  "lastManualActionBy": "ops-user@example.com",
  "resolutionNote": "供应商接口已下线，已转人工确认，不再自动重试"
}
```

状态转移约定：

- `failed` 可以标记为 `dead_letter`。
- 已经是 `dead_letter` 的任务再次调用该接口，可以返回 HTTP `200` 并更新人工字段；不得增加 `deliveryRun` 或清空 attempts。
- `delivering` 不能标记为死信，应返回 HTTP `409`，避免在真实 HTTP 请求仍在进行时误判。
- `queued`、`waiting_retry`、`succeeded` 是否允许标记死信应保持保守；第一版建议返回 HTTP `409`，等任务自然完成或进入 `failed` 后再处置。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 已标记死信，或已是死信并更新人工字段 |
| `401` | 已启用 `NOTIFICATION_API_KEYS`，但未提供正确调用方 API Key |
| `404` | 任务不存在 |
| `409` | 当前状态不允许标记死信，尤其是 `delivering` |
| `500` | 服务内部异常 |

## 8. 批量重试

```text
POST /api/notifications/retry
```

请求体可为空；为空时默认重试 `failed` 任务。普通批量重试不会处理 `dead_letter` 任务，避免把已经人工接管的任务误重新入队。

```json
{
  "status": "failed",
  "limit": 50
}
```

响应：HTTP `200`。

```json
{
  "count": 2,
  "items": [
    {
      "id": "notification-id",
      "status": "queued",
      "deliveryRun": 2,
      "attemptCount": 0
    }
  ]
}
```

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 批量重试完成，可能 `count=0` |
| `400` | 请求体不是 JSON 对象，或 `limit/status` 不合法 |
| `401` | 已启用 `NOTIFICATION_API_KEYS`，但未提供正确调用方 API Key |
| `500` | 服务内部异常 |

## 9. 健康检查

```text
GET /health
```

响应：HTTP `200`。

```json
{
  "status": "ok",
  "serviceVersion": "1.0.0",
  "schemaVersion": "2026-06-10",
  "database": {
    "path": "/path/to/notifications.db",
    "ok": true
  },
  "worker": {
    "alive": true,
    "concurrency": 2,
    "threadCount": 2,
    "aliveCount": 2,
    "pollIntervalSeconds": 1,
    "deliveringLeaseSeconds": 60,
    "startedAt": "2026-06-09T10:00:00+08:00",
    "lastTickAt": "2026-06-09T10:20:00+08:00",
    "lastClaimedJobId": "notification-id",
    "lastClaimedAt": "2026-06-09T10:19:59+08:00",
    "lastLeaseRecoveryAt": null,
    "lastLeaseRecoveryCount": 0,
    "lastError": null
  },
  "queue": {
    "counts": {
      "queued": 0,
      "delivering": 0,
      "waiting_retry": 1,
      "succeeded": 10,
      "failed": 2,
      "dead_letter": 1
    },
    "readyCount": 0,
    "expiredDeliveringCount": 0
  },
  "now": "2026-06-09T10:20:00+08:00"
}
```

`serviceVersion` 标识当前运行的服务构建或发布版本，`schemaVersion` 标识本接口响应结构的契约版本。它们用于页面白屏或新旧前后端混跑时快速判断“请求是否打到了旧服务”或“前端是否按新 schema 渲染”，不代表完整数据库迁移状态，也不替代正式发布、迁移和兼容性治理。

Worker 并发字段兼容口径：

- `worker.concurrency` 表示 `NOTIFICATION_WORKER_CONCURRENCY` 解析后的目标并发数，文档默认值为 `1`。
- `worker.threadCount` 表示当前进程已创建或纳入管理的 Worker 线程数。
- `worker.aliveCount` 表示当前仍存活的 Worker 线程数；多 Worker 场景下应优先用它判断是否有线程异常退出。
- `worker.alive` 可以保留为布尔摘要字段。兼容旧实现时，它可能只表示单个 Worker 线程是否存活；多 Worker 实现中建议当 `aliveCount > 0` 时为 `true`，同时用 `aliveCount < concurrency` 表达部分降级。
- 如果后端采用 `configuredConcurrency`、`runningCount`、`workerCount` 等等价字段名，语义应与上述三类数量保持一致；前端和运维脚本应对这些字段缺失或为 `null` 做兼容，缺失时按单 Worker 旧版本排查。

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 健康检查成功，`status` 通常为 `ok` |
| `500` | 健康检查过程中出现异常，响应体应包含 `status=degraded` 和错误定位信息 |

运维判断：

- `database.ok=false`：数据库不可用，提交和查询都可能失败。
- `worker.alive=false` 或 `worker.lastTickAt` 长期不变：服务进程可能活着，但后台投递停止。
- `worker.aliveCount < worker.concurrency`：配置了多 Worker，但部分线程未存活，应检查线程异常、启动失败或未兼容该字段的旧服务。
- `worker.concurrency` 高于 `1` 且 `queue.readyCount` 仍持续增长：瓶颈可能在 SQLite 锁、供应商限流、网络或投递超时，不应只靠继续调高线程数解决。
- `queue.readyCount` 持续大于 0：有任务已到投递时间但没有被及时领取。
- `queue.counts.waiting_retry` 持续增长：供应商短期失败或限流。
- `queue.counts.failed` 持续增长：自动重试耗尽，需要人工、批量重试或供应商排障。
- `queue.counts.dead_letter` 持续增长：失败任务正在被人工接管，需要检查是否存在供应商长期不可用、配置错误或缺少后续工单流程。
- `queue.expiredDeliveringCount` 大于 0 或 `worker.lastLeaseRecoveryCount` 增加：存在卡住的 `delivering` 任务被租约回收。

## 10. 统计接口

`/api/stats` 用于看趋势和聚合指标，例如总任务数、平均尝试次数、最近失败数量和错误类型分布。单任务定位仍以详情和 attempts 为准。

```text
GET /api/stats
```

响应：HTTP `200`。

```json
{
  "status": "ok",
  "serviceVersion": "1.0.0",
  "schemaVersion": "2026-06-10",
  "queue": {
    "counts": {
      "queued": 4,
      "delivering": 1,
      "waiting_retry": 10,
      "succeeded": 80,
      "failed": 5,
      "dead_letter": 2
    },
    "readyCount": 4,
    "expiredDeliveringCount": 0
  },
  "notifications": {
    "total": 100,
    "averageAttempts": 1.25
  },
  "attempts": {
    "total": 125,
    "averagePerNotification": 1.25,
    "averageSequence": 1.5,
    "recentErrorCount": 5,
    "recentErrorWindowSeconds": 3600,
    "recentErrorsByType": {
      "http_error": 3,
      "timeout": 1,
      "network_error": 1
    }
  },
  "now": "2026-06-09T10:20:00Z"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `serviceVersion` | 当前运行服务版本，用于排查旧服务/新前端错配 |
| `schemaVersion` | 当前统计响应结构版本，用于排查前端按错误 schema 解析 |
| `queue.counts` | 各任务状态数量 |
| `queue.readyCount` | 当前可被 Worker 领取的任务数量 |
| `queue.expiredDeliveringCount` | 当前已超出租约但尚未回收的 `delivering` 数量 |
| `notifications.total` | 通知任务总数 |
| `notifications.averageAttempts` | 任务当前轮次平均尝试次数 |
| `attempts.total` | attempts 历史记录总数 |
| `attempts.averagePerNotification` | 每个任务平均 attempts 数 |
| `attempts.averageSequence` | attempts 全局序号平均值，主要用于粗略判断历史重试密度 |
| `attempts.recentErrorCount` | 最近窗口内失败 attempt 数 |
| `attempts.recentErrorWindowSeconds` | 最近错误统计窗口，当前为 3600 秒 |
| `attempts.recentErrorsByType` | 最近窗口内按 `errorType` 聚合的失败数量 |
| `now` | 统计生成时间 |

状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 统计成功 |
| `500` | 数据库或统计计算异常 |

`/api/stats` 应用于趋势、聚合和看板；单任务定位仍以 `/api/notifications/{id}` 和 `/api/notifications/{id}/attempts` 为准。

`/api/stats` 中的 `serviceVersion` 和 `schemaVersion` 与 `/health` 口径一致，只用于错配排查和人工诊断，不等同完整迁移系统。

## 11. Prometheus 文本指标草案

本节是任务板 74 的演进草案，不代表当前代码已经实现 `/metrics`，也不要求当前 Demo 引入 Prometheus 客户端依赖。现阶段可先用 `/health` 和 `/api/stats` 的 JSON 字段支撑页面、人工排障和轻量看板。

如果未来增加 Prometheus text exposition，可以从现有接口映射出以下指标示例：

| 指标名 | 类型建议 | 来源字段 | 说明 |
| --- | --- | --- | --- |
| `notification_service_up` | gauge | `/health.status` | `status=ok` 时为 `1`，降级或采集失败时为 `0` |
| `notification_database_up` | gauge | `/health.database.ok` | 数据库可查询为 `1`，不可用为 `0` |
| `notification_worker_alive` | gauge | `/health.worker.alive` | Worker 线程存活为 `1`，否则为 `0` |
| `notification_worker_poll_interval_seconds` | gauge | `/health.worker.pollIntervalSeconds` | Worker 轮询间隔 |
| `notification_worker_delivering_lease_seconds` | gauge | `/health.worker.deliveringLeaseSeconds` | `delivering` 租约阈值 |
| `notification_worker_last_lease_recovery_count` | gauge | `/health.worker.lastLeaseRecoveryCount` | 最近一次租约回收数量，不是累计 counter |
| `notification_queue_status_total{status}` | gauge | `/health.queue.counts` 或 `/api/stats.queue.counts` | 各状态当前任务数量 |
| `notification_queue_ready_total` | gauge | `/health.queue.readyCount` | 当前可被 Worker 领取的任务数量 |
| `notification_queue_expired_delivering_total` | gauge | `/health.queue.expiredDeliveringCount` | 当前已超出租约但尚未回收的 `delivering` 数量 |
| `notification_tasks_total` | gauge | `/api/stats.notifications.total` | 当前数据库中的通知任务总数 |
| `notification_task_average_attempts` | gauge | `/api/stats.notifications.averageAttempts` | 当前轮次平均尝试次数 |
| `notification_attempts_total` | gauge | `/api/stats.attempts.total` | attempts 历史记录总数 |
| `notification_attempts_average_per_task` | gauge | `/api/stats.attempts.averagePerNotification` | 每个任务平均 attempts 数 |
| `notification_attempts_recent_errors_total` | gauge | `/api/stats.attempts.recentErrorCount` | 最近窗口内失败 attempt 数 |
| `notification_attempts_recent_errors_by_type_total{error_type}` | gauge | `/api/stats.attempts.recentErrorsByType` | 最近窗口内按错误类型聚合的失败数量 |

文本格式示例：

```text
# HELP notification_queue_ready_total Current number of ready notification tasks.
# TYPE notification_queue_ready_total gauge
notification_queue_ready_total 4
# HELP notification_queue_status_total Current notification tasks by status.
# TYPE notification_queue_status_total gauge
notification_queue_status_total{status="queued"} 4
notification_queue_status_total{status="failed"} 5
# HELP notification_attempts_recent_errors_by_type_total Recent failed attempts by error type.
# TYPE notification_attempts_recent_errors_by_type_total gauge
notification_attempts_recent_errors_by_type_total{error_type="timeout"} 1
```

暂不引入 Prometheus 依赖的原因：

- 当前 Demo 坚持 Python 标准库和本地零依赖，便于评审和快速运行。
- `/health` 和 `/api/stats` 已覆盖页面、人工排障和轻量统计的第一版需求。
- 指标命名、标签基数、部署拓扑和告警规则尚未稳定，过早引入客户端库容易形成需要兼容的错误契约。
- 生产 Prometheus 接入通常还需要 scrape 配置、实例标签、权限和告警路由，这些超出当前代码范围。

未来演进条件：

- 服务进入多实例或准生产部署，需要按实例、版本和环境采集健康与队列指标。
- 团队已经有 Prometheus/Grafana 基础设施，并明确 scrape 路径、保留周期和告警路由。
- 需要对失败率、队列堆积、投递延迟、Worker 心跳、超时数量设置自动告警。
- 指标标签基数规则明确：允许 `status`、`error_type`、`service_version`、`schema_version` 等低基数字段，禁止 `requestId`、`targetUrl`、用户 ID 或原始错误文本进入标签。
- 需要补充真实 counter 和 histogram，例如投递请求总数、投递耗时分布和按错误类型累计失败数，而不只从 JSON 快照映射 gauge。
