# 后端功能点与代码关联说明

本文只描述后端功能点与代码模块的对应关系，不覆盖前端实现。

当前后端已经从单文件拆分为 `notification_service/` 包。整体思路是：`http_handler.py` 作为 Controller 接收 HTTP 请求，`service.py` 作为用例层处理业务命令和查询，`database.py` 负责 SQLite 存储和数据转换，`worker.py` 负责异步投递状态机，`security.py` 和 `auth.py` 负责安全边界，`metrics.py` 负责健康检查和统计读模型。

## 1. 后端模块总览

| 模块 | 主要职责 |
| --- | --- |
| `server.py` | 程序入口，只调用 `notification_service.app.main()`。 |
| `notification_service/app.py` | 解析启动参数、初始化数据库、启动 WorkerPool、创建 `ThreadingHTTPServer`。 |
| `notification_service/settings.py` | 集中管理常量、环境变量、超时、租约、Worker 并发、版本号和导出字段。 |
| `notification_service/http_handler.py` | HTTP Controller：路由分发、JSON/CSV 响应、静态文件托管、本地 mock vendor。 |
| `notification_service/service.py` | 通知用例层：创建、查询、过滤、导出、单条重试、批量重试、死信处理。 |
| `notification_service/database.py` | SQLite 连接、表结构初始化、兼容性迁移、行对象转换、队列统计。 |
| `notification_service/worker.py` | 异步投递 Worker、任务领取、HTTP 投递、失败重试、attempt 写入、租约回收。 |
| `notification_service/security.py` | 目标 URL 校验、Origin 白名单、SSRF 防护、重定向复检、敏感字段脱敏。 |
| `notification_service/auth.py` | 可选调用方 API Key 鉴权。 |
| `notification_service/metrics.py` | `/health` 和 `/api/stats` 的聚合读模型。 |
| `notification_service/time_utils.py` | 时间戳、ISO 时间格式化、耗时计算。 |

## 2. 功能点到代码映射

| 功能点 | 入口/接口 | 核心代码位置 | 说明 |
| --- | --- | --- | --- |
| 服务启动 | `python3 server.py --host ... --port ...` | `server.py`、`app.py` | `server.py` 是薄入口；`app.run()` 初始化 DB、启动 WorkerPool、启动 HTTP 服务。 |
| 配置读取 | 环境变量 | `settings.py` | 负责 `NOTIFICATION_DB_PATH`、`NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`、`NOTIFICATION_DELIVERING_LEASE_SECONDS`、`NOTIFICATION_WORKER_CONCURRENCY` 等配置解析。 |
| 数据库初始化与迁移 | 服务启动时自动执行 | `database.init_db()`、`database.ensure_column()` | 创建 `notifications`、`notification_attempts` 表；通过 `ensure_column()` 兼容旧库字段。 |
| 创建通知任务 | `POST /api/notifications` | `http_handler.NotificationHandler.do_POST()`、`service.create_notification()` | Controller 读取 JSON 后调用用例层；用例层校验请求、写入 `notifications`，初始状态为 `queued`。 |
| 请求 Header/Body 规范化 | 创建通知时 | `service.normalize_headers()`、`service.normalize_body()` | Header 必须是对象；Body 支持对象、数组、字符串、数字、布尔和空值；JSON Body 自动补 `Content-Type`。 |
| `requestId` 幂等 | `POST /api/notifications` | `service.normalize_request_id()`、`service.create_notification()` | 如果 `requestId` 已存在，返回旧任务并标记 duplicate，不重复创建内部任务。 |
| 目标 URL 校验 | 创建任务和投递前 | `security.validate_target_url()` | 校验绝对 HTTP(S) URL、禁止用户名密码、执行白名单和 SSRF 检查。 |
| Origin 白名单 | `NOTIFICATION_ALLOWED_TARGETS` | `security.parse_allowed_target_origins()`、`security.validate_target_url()` | 只允许精确 Origin；未配置时不启用外部 Origin 白名单限制，但仍保留 SSRF 防护。 |
| 同源 mock vendor 例外 | `/mock/vendor/*` | `security.is_same_origin_mock_vendor()`、`http_handler.handle_mock_vendor()` | 允许当前服务同源 mock vendor，用于本地演示和 smoke test。 |
| SSRF 基础防护 | 创建任务和重定向前 | `security.resolve_target_addresses()`、`security.is_blocked_target_address()`、`security.assert_public_resolved_target()` | 拦截 loopback、private、link-local、reserved 等地址，避免通知服务变成内网探测代理。 |
| 重定向目标二次校验 | Worker HTTP 投递时 | `security.SafeRedirectHandler`、`security.build_safe_delivery_opener()` | 跟随供应商重定向前再次调用 `validate_target_url()`，防止外部 URL 跳转到被禁止地址。 |
| 调用方 API Key 鉴权 | 写接口 | `auth.ApiKeyAuthenticator`、`http_handler.do_POST()` | 设置 `NOTIFICATION_API_KEYS` 后，创建、重试、批量重试、死信写接口需要 `X-Notification-Api-Key` 或 Bearer Key。 |
| 查询任务列表 | `GET /api/notifications` | `http_handler.do_GET()`、`service.list_notifications()`、`service.query_notification_rows()` | 支持状态、事件类型、来源系统、目标地址关键词、时间范围、分页和排序。 |
| 查询任务详情 | `GET /api/notifications/{id}` | `http_handler.do_GET()`、`service.get_notification()`、`database.row_to_dict()` | 返回单任务完整信息，Header/Body/错误字段会脱敏。 |
| 查询投递尝试 | `GET /api/notifications/{id}/attempts` | `service.get_notification_attempts()`、`database.attempt_row_to_dict()` | 返回每次 attempt 的序号、状态、HTTP 状态码、耗时、错误分类和时间。 |
| CSV 导出 | `GET /api/notifications/export.csv` | `service.export_notifications_csv()` | 复用列表过滤和排序参数，导出排障字段，不导出 Header 原文和完整 Body。 |
| 异步投递 Worker | 后台线程 | `worker.DeliveryWorkerPool`、`worker.worker_loop()` | Worker 循环领取 ready 任务，执行 HTTP 投递，写状态和 attempts。 |
| Worker 并发 | `NOTIFICATION_WORKER_CONCURRENCY` | `settings.notification_worker_concurrency()`、`worker.DeliveryWorkerPool.start()` | 启动多个后台线程；SQLite 写入仍由 `DB_LOCK` 串行保护。 |
| 任务领取 | Worker 内部 | `worker.claim_next_job()` | 从 `queued`、`waiting_retry` 中选择到期任务，原子更新为 `delivering`。 |
| HTTP 投递 | Worker 内部 | `worker.deliver_job()` | 构造 `urllib.request.Request`，使用安全 opener 投递到 `target_url`。 |
| 成功处理 | Worker 内部 | `worker.mark_success()` | 2xx 响应后更新任务为 `succeeded`，清空错误，写入成功 attempt。 |
| 失败处理与分类 | Worker 内部 | `worker.deliver_job()`、`worker.mark_failure()` | 非 2xx、HTTPError、URLError、TimeoutError、InvalidTargetError 分别映射为 `http_error`、`network_error`、`timeout`、`invalid_target` 等。 |
| 指数退避重试 | Worker 内部 | `worker.backoff_seconds()`、`worker.mark_failure()` | 未达到最大尝试次数时进入 `waiting_retry`，设置 `next_attempt_at`；达到上限后进入 `failed`。 |
| 单任务 timeout | 创建任务和投递时 | `service.clamp_timeout_seconds()`、`worker.deliver_job()` | `timeoutSeconds` 优先于全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`，并限制在安全范围内。 |
| 手动单条重试 | `POST /api/notifications/{id}/retry` | `service.retry_notification()` | 将任务重新入队，重置 attempt 计数，递增 `delivery_run`，记录人工动作字段。 |
| 批量重试 | `POST /api/notifications/retry` | `service.retry_notifications_batch()` | 按状态筛选失败类任务批量重置为 `queued`，适合供应商恢复后补偿。 |
| 死信/人工处理 | `POST /api/notifications/{id}/dead-letter` | `service.mark_dead_letter()` | 将符合条件的任务改为 `dead_letter`，停止自动投递，并记录处理人和备注。 |
| 人工动作审计字段 | retry/dead-letter | `database.init_db()`、`service.retry_notification()`、`service.mark_dead_letter()` | 字段包括 `last_manual_action`、`last_manual_action_at`、`last_manual_action_by`、`resolution_note`。 |
| `delivery_run` 投递轮次 | 手动重试、attempts | `database.init_db()`、`worker.insert_attempt()`、`service.retry_notification()` | 每次人工重新入队递增 `delivery_run`，attempt 记录保留本轮次。 |
| attempt 全局序号 | attempts | `worker.insert_attempt()`、`database.backfill_attempt_sequences()` | `attempt_sequence` 在单任务维度递增，避免人工重试后 attempt 编号混淆。 |
| 服务重启恢复 | 服务启动时 | `database.init_db()` | 启动时把遗留的 `delivering` 任务恢复为 `queued`，避免进程退出导致任务永久卡住。 |
| delivering 租约回收 | Worker 循环 | `worker.reclaim_expired_deliveries()`、`database.expired_delivering_cutoff()` | 长时间卡在 `delivering` 的任务会重新入队或标记失败，并写入 `lease_timeout` attempt。 |
| 健康检查 | `GET /health` | `metrics.get_health_payload()`、`database.get_queue_summary()`、`worker.worker_runtime_snapshot()` | 返回 DB、Worker 存活、并发、队列计数、readyCount、expiredDeliveringCount 等。 |
| 统计接口 | `GET /api/stats` | `metrics.get_stats_payload()` | 返回任务总数、平均尝试次数、attempt 总量、最近失败数和错误类型分布。 |
| 敏感信息脱敏 | 详情、列表、attempts、CSV | `security.redact_headers()`、`security.redact_body_for_api()`、`security.redact_query_secrets()`、`database.row_to_dict()` | 对 token、authorization、secret、password、key 等字段脱敏，避免页面和 API 泄露凭证。 |
| 本地 mock vendor | `/mock/vendor/{name}` | `http_handler.handle_mock_vendor()` | 支持成功、失败、指定 HTTP 状态码、延迟、重定向，用于本地调试和 smoke test。 |
| 静态文件托管 | `/`、`/public assets` | `http_handler.serve_static()` | 由同一个后端服务托管前端静态文件，并限制路径必须位于 `PUBLIC_DIR` 下。 |
| CORS/OPTIONS | 所有接口 | `http_handler.do_OPTIONS()`、`http_handler.add_common_headers()` | 允许常用方法和 `X-Notification-Api-Key` Header，方便本地调试和跨域调用。 |

## 3. 主要调用链

### 3.1 创建通知

```text
HTTP POST /api/notifications
  -> http_handler.NotificationHandler.do_POST()
  -> auth.ApiKeyAuthenticator 检查写接口鉴权
  -> service.NotificationService.create()
  -> service.create_notification()
  -> security.validate_target_url()
  -> database.get_db() 写入 notifications
```

核心结果：任务落库为 `queued`，业务系统收到任务 ID；后台 Worker 稍后异步投递。

### 3.2 后台投递

```text
app.run()
  -> worker.DeliveryWorkerPool.start()
  -> worker.worker_loop()
  -> worker.claim_next_job()
  -> worker.deliver_job()
  -> security.SafeRedirectHandler 复检重定向目标
  -> worker.mark_success() / worker.mark_failure()
  -> worker.insert_attempt()
```

核心结果：任务在 `queued -> delivering -> succeeded` 或 `queued -> delivering -> waiting_retry/failed` 之间流转，并保留 attempts。

### 3.3 手动补偿

```text
POST /api/notifications/{id}/retry
  -> http_handler.NotificationHandler.do_POST()
  -> service.NotificationService.retry_one()
  -> service.retry_notification()
  -> 更新 notifications 为 queued，delivery_run + 1

POST /api/notifications/{id}/dead-letter
  -> http_handler.NotificationHandler.do_POST()
  -> service.NotificationService.dead_letter()
  -> service.mark_dead_letter()
  -> 更新 notifications 为 dead_letter
```

核心结果：失败任务可以被人工重新入队，也可以转入死信状态停止自动投递。

### 3.4 查询和排障

```text
GET /api/notifications
  -> service.NotificationService.list()
  -> service.query_notification_rows()
  -> database.row_to_dict()

GET /api/notifications/{id}
  -> service.NotificationService.get()
  -> database.row_to_dict()

GET /api/notifications/{id}/attempts
  -> service.NotificationService.attempts()
  -> database.attempt_row_to_dict()

GET /health / GET /api/stats
  -> metrics.HealthReporter
  -> database.get_queue_summary()
  -> worker.worker_runtime_snapshot()
```

核心结果：列表、详情、attempts、健康检查和统计接口共同支撑白屏调试、失败排查和运维判断。

## 4. 数据表与关键字段

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `notifications` | 存储通知任务当前状态和请求内容 | `id`、`request_id`、`target_url`、`method`、`headers_json`、`body`、`status`、`attempt_count`、`delivery_run`、`max_attempts`、`timeout_seconds`、`next_attempt_at`、`failure_type`、`last_error`、`last_status_code`、`last_manual_action*`、`resolution_note` |
| `notification_attempts` | 存储每一次投递尝试记录 | `notification_id`、`attempt_number`、`attempt_sequence`、`delivery_run`、`status`、`status_code`、`error`、`error_type`、`duration_ms`、`created_at` |

## 5. 结构上的设计模式使用

| 模式/角色 | 当前代码体现 | 作用 |
| --- | --- | --- |
| Controller | `http_handler.NotificationHandler` | 只处理 HTTP 路由、状态码和响应格式。 |
| Facade / Use Case Service | `service.NotificationService`、`metrics.HealthReporter` | 给 Controller 提供稳定调用面，隐藏 SQL 和状态流转细节。 |
| Repository/Gateway | `database.py`、`NotificationDatabase` | 集中处理数据库连接、迁移、行转换和队列统计。 |
| Policy | `security.TargetSecurityPolicy`、`auth.ApiKeyAuthenticator` | 把目标安全规则和鉴权规则从业务流程中分离出来。 |
| Worker Pool | `worker.DeliveryWorkerPool` | 管理后台投递线程生命周期和并发配置。 |
| State Machine | `worker.claim_next_job()`、`mark_success()`、`mark_failure()`、`service.retry_notification()` | 控制任务在 `queued`、`delivering`、`waiting_retry`、`succeeded`、`failed`、`dead_letter` 之间流转。 |

## 6. 阅读代码建议

如果从功能理解代码，可以按下面顺序阅读：

1. `http_handler.py`：先看有哪些后端接口。
2. `service.py`：再看每个接口背后的业务动作。
3. `database.py`：理解数据表、字段和 API 响应转换。
4. `worker.py`：理解异步投递、重试和 attempts。
5. `security.py`、`auth.py`：理解安全边界。
6. `metrics.py`：理解健康检查和统计接口。
