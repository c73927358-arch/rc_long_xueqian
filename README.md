# HTTP 通知投递服务 Demo

这是一个模拟企业内部外部 HTTP 通知投递服务的小型实现：

- 后端：`server.py` 是启动入口，核心代码拆分在 `notification_service/`，Python 标准库实现，无第三方依赖。
- 数据库：SQLite，启动后自动生成 `notifications.db`。
- 前端：`public/`，由后端直接托管；脚本按职责拆分在 `public/js/`。
- 需求文档：[docs/requirements.md](docs/requirements.md)
- 设计说明：[docs/design-notes.md](docs/design-notes.md)
- 测试文档：[docs/test-plan.md](docs/test-plan.md)
- API 契约：[docs/api-contract.md](docs/api-contract.md)
- API 接入示例：[docs/api-examples.md](docs/api-examples.md)
- Agent 协作计划：[docs/agent-loop-plan.md](docs/agent-loop-plan.md)
- AI 使用说明：[docs/ai-usage-notes.md](docs/ai-usage-notes.md)

## 代码结构

- `server.py`：薄入口，只负责调用应用启动。
- `notification_service/settings.py`：配置、常量和环境变量解析。
- `notification_service/auth.py`：调用方 API Key 鉴权，使用共享 Key Authenticator。
- `notification_service/security.py`：目标 URL 校验、SSRF 防护、重定向校验和脱敏策略。
- `notification_service/database.py`：SQLite 连接、迁移、行对象转换和队列统计。
- `notification_service/service.py`：通知用例层，承担创建、查询、导出、重试和死信处理。
- `notification_service/worker.py`：投递 Worker、重试状态机、attempt 写入和 WorkerPool。
- `notification_service/metrics.py`：`/health` 和 `/api/stats` 的读模型。
- `notification_service/http_handler.py`：HTTP Controller，负责路由、JSON/CSV 响应、静态文件和 mock vendor。
- `public/js/`：前端按 `context`、`utils`、`health`、`form`、`filters`、`list`、`detail`、`examples`、`bootstrap` 拆分，避免继续堆在一个 `app.js` 文件里。

## 设计摘要

本系统边界是内部业务系统到外部 HTTP(S) API 的通知投递层：负责接收、持久化、异步投递、失败重试、状态查询、投递尝试记录、安全边界和人工补偿。第一版不解决精确一次投递、供应商配置中心、复杂认证签名、分布式高可用和完整权限审计，因为这些能力需要真实供应商契约、基础设施和运维配套，过早引入会掩盖核心投递问题。

当前投递语义选择“至少一次”。任务写入 SQLite 后才返回业务系统，后台 Worker 投递失败时按指数退避重试，超过最大次数后进入 `failed`，并保留错误原因；如果外部系统长期不可用，第一版通过有限自动重试、手动重试和 `dead_letter` 人工处置标记处理，生产化时再增加完整死信队列、工单联动、供应商级熔断和告警。

当前 Demo 没有引入 Kafka、RabbitMQ、Temporal 等中间件，原因是作业重点在工程判断和可靠性语义，本地零依赖更容易运行和验证。未来流量增长时，可以演进为 PostgreSQL/消息队列 + 多 Worker + 死信队列 + 可观测性平台。完整取舍见设计说明文档。

当前 Demo 已经包含基础目标地址安全：默认允许当前页面同源的 `/mock/vendor/*` 作为本地演示目标；其他 localhost、私网、link-local、metadata 类地址会被拒绝。生产接入外部供应商时，可以通过 `NOTIFICATION_ALLOWED_TARGETS` 配置精确 Origin 白名单。

调用方 API Key 鉴权是可选的最小保护：默认关闭；设置 `NOTIFICATION_API_KEYS` 后，创建通知、手动重试、标记死信和批量重试这些写接口都需要 `X-Notification-Api-Key` 或 `Authorization: Bearer ...`。查询、导出、详情、attempts、`/health`、`/api/stats`、静态页面和本地 mock vendor 暂不受该 Key 保护。它不是用户身份、RBAC 或审计系统，生产化仍应继续补权限分级、密钥轮换、操作审计和网关/mTLS 等能力。

第一版让业务系统显式传入 `targetUrl`、Header 和 Body，是为了先验证可靠投递链路。后续供应商数量增长后，可以演进出 `vendorKey`：业务方只传供应商标识和业务 Body，由通知服务从配置中解析目标地址、默认 Header、超时、重试和白名单绑定，降低重复配置和误投风险。这个方向已写入设计说明和接入示例，但当前后端不要求实现。

运维判断可以通过 `/health` 和 `/api/stats` 完成：`/health` 关注 `database.ok`、`worker.alive`、`worker.concurrency`、`worker.threadCount`、`worker.aliveCount`、`queue.counts`、`queue.readyCount`、`queue.expiredDeliveringCount`、`worker.lastLeaseRecoveryCount`、`worker.lastError` 等运行状态；`/api/stats` 提供任务总量、平均尝试次数、最近失败数和错误类型分布。两个接口中的 `serviceVersion` 和 `schemaVersion` 用于排查“旧服务/新前端”错配，不等同完整数据库迁移或版本治理系统。统计接口服务于趋势和聚合指标，不替代单任务详情和 attempts。

Prometheus 文本指标目前只是演进草案，当前代码不实现 `/metrics`，也不引入 Prometheus 依赖。可映射指标名、来源字段和未来引入条件见 [docs/api-contract.md](docs/api-contract.md)。

列表分页、排序、时间范围过滤和 CSV 导出可以通过首页任务列表调试，也可以直接访问 `/api/notifications?limit=20&offset=0&sort=createdAt&order=desc` 和 `/api/notifications/export.csv` 验证。列表与 CSV 共享 `createdFrom`、`createdTo`、`updatedFrom`、`updatedTo` 时间范围参数；非法时间返回 `400`。导出只用于排障视图，不包含 Header 原文或完整 Body 原文。

详情抽屉提供复制 `requestId` 和 `targetUrl` 的调试入口。页面白屏或列表过滤异常时，可以把这两个字段与 Network 面板、`GET /api/notifications/{id}`、`GET /api/notifications/{id}/attempts` 的结果对照，快速判断是前端渲染/复制控件问题，还是后端详情数据或目标地址数据异常。

## 启动

```bash
python3 server.py --host 127.0.0.1 --port 8000
```

如果 8000 已被占用，可以换成 8001 或其他空闲端口：

```bash
python3 server.py --host 127.0.0.1 --port 8001
```

打开：

```text
http://127.0.0.1:8000/
```

如果按上面的备用端口启动，则打开：

```text
http://127.0.0.1:8001/
```

## 配置

使用临时数据库路径，适合测试脚本或隔离运行：

```bash
NOTIFICATION_DB_PATH=/tmp/notifications.db python3 server.py --host 127.0.0.1 --port 8001
```

开启调用方 API Key 鉴权，多个 Key 建议用逗号分隔：

```bash
NOTIFICATION_API_KEYS=dev-caller-key,ops-caller-key python3 server.py --host 127.0.0.1 --port 8001
```

开启后，写接口必须携带以下任一 Header：

```text
X-Notification-Api-Key: dev-caller-key
Authorization: Bearer dev-caller-key
```

缺失或错误时返回 HTTP `401`，响应体为 `{"error":"unauthorized"}`，且不会创建任务或修改任务状态。调用方 Key 只用于保护通知服务写入口，不会被投递给供应商；供应商侧认证仍放在通知 payload 的 `headers` 中。不要把真实 Key 写进日志、截图、工单、`resolutionNote` 或示例文档。

限制外部通知目标，只允许精确 Origin：

```bash
NOTIFICATION_ALLOWED_TARGETS=https://api.vendor.example,https://crm.vendor.example \
  python3 server.py --host 127.0.0.1 --port 8001
```

本地演示默认允许同源 `/mock/vendor/*`。如需关闭：

```bash
ALLOW_LOCAL_MOCK_VENDOR=false python3 server.py --host 127.0.0.1 --port 8001
```

配置单次投递到供应商的 HTTP 超时时间：

```bash
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=3 python3 server.py --host 127.0.0.1 --port 8001
```

`NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` 是全局默认值，默认 `8.0` 秒，非法环境变量会回退默认值，边界会限制在 `0.1` 到 `60.0` 秒。创建通知时也可以传 `timeoutSeconds` 覆盖单个任务的供应商 HTTP 请求超时；省略或传 `null` 时使用全局默认，非法类型会让 `POST /api/notifications` 返回 `400`，合法数值同样限制在 `0.1` 到 `60.0` 秒。任务级覆盖适合少数供应商 SLA 明确更短或调用成本较高的场景。

配置后台投递 Worker 线程并发数：

```bash
NOTIFICATION_WORKER_CONCURRENCY=2 python3 server.py --host 127.0.0.1 --port 8001
```

`NOTIFICATION_WORKER_CONCURRENCY` 的文档约定默认值为 `1`，表示保持单 Worker 线程投递，最适合本地 Demo、SQLite 文件数据库和排障复现。建议范围是 `1` 到 `4`；只有在 `/health.queue.readyCount` 长时间大于 0、供应商响应稳定、CPU/网络仍有余量，并且确认下游能处理至少一次语义下的重复通知时，才逐步调大。对 SQLite + `DB_LOCK` 的第一版实现，调大会提高对慢供应商的并行等待能力，但数据库写入和领取仍会被锁串行化；过高并发可能放大供应商限流、HTTP 超时、SQLite 锁等待、重复投递窗口和日志噪声。生产流量继续上升时，应演进到 PostgreSQL 行锁或消息队列/调度系统，而不是无限提高本地线程数。

当前实现还会回收卡在 `delivering` 的任务：服务启动时会恢复上一次进程退出前遗留的投递中任务；Worker 运行中也会按 `NOTIFICATION_DELIVERING_LEASE_SECONDS` 扫描超出租约的 `delivering` 任务并重新入队或标记失败。默认租约为 60 秒，详见 [API 接入示例](docs/api-examples.md) 和 [测试文档](docs/test-plan.md)。

## API 示例

完整接口字段和状态码见 [docs/api-contract.md](docs/api-contract.md)。接入示例见 [docs/api-examples.md](docs/api-examples.md)，其中覆盖：

- 用户通过第三方广告系统引流并成功注册后，通知广告系统记录转化。
- 用户订阅付款成功后，通知 CRM 系统更改 Contact 状态。
- 用户购买商品后，通知库存系统进行库存变更。

提交通知：

```bash
curl -X POST http://127.0.0.1:8000/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8000/mock/vendor/crm",
    "headers": {"X-Vendor-Token": "demo-token"},
    "body": {"contactId": "C-10086", "status": "paid"},
    "maxAttempts": 5,
    "timeoutSeconds": 3
  }'
```

单任务超时覆盖示例，适合验证慢供应商快速失败：

```bash
curl -X POST http://127.0.0.1:8000/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "eventType": "subscription.paid",
    "sourceSystem": "billing-service",
    "targetUrl": "http://127.0.0.1:8000/mock/vendor/crm?delayMs=3000",
    "body": {"contactId": "C-10086", "status": "paid"},
    "maxAttempts": 1,
    "timeoutSeconds": 1
  }'
```

查看任务：

```bash
curl http://127.0.0.1:8000/api/notifications
```

按创建/更新时间范围查询：

```bash
curl "http://127.0.0.1:8000/api/notifications?createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z"
```

按同一时间范围导出 CSV：

```bash
curl -o notifications.csv "http://127.0.0.1:8000/api/notifications/export.csv?createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

查看单任务投递尝试：

```bash
curl http://127.0.0.1:8000/api/notifications/{id}/attempts
```

手动重试：

```bash
curl -X POST http://127.0.0.1:8000/api/notifications/{id}/retry
```

标记死信/人工处理：

```bash
curl -X POST http://127.0.0.1:8000/api/notifications/{id}/dead-letter \
  -H 'Content-Type: application/json' \
  -d '{
    "actionBy": "ops-user@example.com",
    "resolutionNote": "供应商接口长期不可用，已转人工确认"
  }'
```

死信任务状态为 `dead_letter`，不会被 Worker 自动投递，也不会被普通批量重试误重新入队；需要恢复时，对该任务执行单条 `POST /api/notifications/{id}/retry`。

## 错误处理口径

需要区分两类失败：

| 类型 | 发生阶段 | 判断方式 | 处理建议 |
| --- | --- | --- | --- |
| 业务系统提交失败 | 调用 `POST /api/notifications` 时 | 返回 `400`、`404`、`409`、`500` 或网络错误 | `400` 先修正参数；`500` 或网络错误可按业务系统策略重试；只有 `201` 或 `200 duplicate=true` 才视为已接收 |
| 外部供应商投递失败 | 任务已创建后由 Worker 投递时 | 任务状态变为 `waiting_retry` 或 `failed`，并记录 `failureType` 和 attempts | 业务主流程不阻塞；由通知服务自动重试，最终失败后人工或批量重试 |

常见提交响应：

| 状态码 | 含义 |
| --- | --- |
| `201` | 新通知任务已创建并持久化 |
| `200` + `duplicate=true` | 同一 `requestId` 已存在，命中幂等 |
| `400` | 请求 JSON、URL、Header、Method、白名单或参数校验失败 |
| `401` | 已设置 `NOTIFICATION_API_KEYS`，但写接口未提供正确调用方 API Key |
| `404` | 查询或重试的任务不存在，或 API 路径错误 |
| `409` | 正在 `delivering` 的任务暂不能手动重试或标记死信 |
| `500` | 服务内部异常，例如数据库不可用 |

人工处置字段包括 `lastManualAction`、`lastManualActionAt`、`lastManualActionBy` 和 `resolutionNote`。页面详情和 API 详情都应能用这些字段判断最近一次人工动作是谁在什么时候做的，以及为什么处理。

## Mock 供应商

成功投递地址：

```text
http://127.0.0.1:8000/mock/vendor/crm
```

失败投递地址：

```text
http://127.0.0.1:8000/mock/vendor/crm?fail=1
```

## 测试

运行自动 smoke test：

```bash
python3 scripts/smoke_test.py
```

对已经启动的服务执行：

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8001
```
