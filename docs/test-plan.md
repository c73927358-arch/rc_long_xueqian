# HTTP 通知投递服务测试文档

## 1. 本地页面调试入口

先启动服务：

```bash
python3 server.py --host 127.0.0.1 --port 8001
```

打开页面：

```text
http://127.0.0.1:8001/
```

如果你启动时使用的是 8000 端口，则打开：

```text
http://127.0.0.1:8000/
```

健康检查接口：

```text
http://127.0.0.1:8001/health
```

如果页面白屏，先检查以下三个地址是否都能正常返回：

```text
http://127.0.0.1:8001/
http://127.0.0.1:8001/app.js
http://127.0.0.1:8001/styles.css
```

## 2. 页面上如何操作

页面左侧是通知提交表单，右侧是投递任务列表。

常用按钮：

| 按钮 | 位置 | 作用 |
| --- | --- | --- |
| `成功示例` | 表单底部 | 自动填充一个会投递成功的 mock 供应商请求 |
| `失败示例` | 表单底部 | 自动填充一个会返回 HTTP 500 的 mock 供应商请求 |
| `提交通知` | 表单底部右侧 | 将当前表单内容提交到内部通知服务 |
| `刷新` | 任务列表右上角 | 重新加载任务列表 |
| `重新投递` | 每个任务卡片底部 | 将该任务重新入队，再次投递 |
| `标记死信` | 失败任务详情或操作区 | 将已失败任务交给人工处理，不再自动投递 |
| `复制 ID` | 每个任务卡片底部 | 复制任务 ID |

状态筛选按钮：

| 筛选 | 用途 |
| --- | --- |
| `全部` | 查看所有任务 |
| `等待` | 查看还未开始投递的任务 |
| `重试` | 查看本次失败、等待下次自动重试的任务 |
| `成功` | 查看投递成功的任务 |
| `失败` | 查看超过最大次数后最终失败的任务 |
| `死信` | 查看已经人工接管、不再自动投递的任务 |

## 3. 如何判断是否投递成功

提交后，在右侧任务列表查看新任务卡片。

投递成功的判断标准：

- 卡片右上角状态显示 `投递成功`。
- 点击 `成功` 筛选后能看到该任务。
- 任务卡片中的 `尝试` 通常显示为 `1/5` 或 `1/2`。
- `下次投递` 显示为 `-`。

投递失败的判断标准：

- 短暂失败但仍会重试时，状态显示 `等待重试`。
- 达到最大尝试次数后，状态显示 `最终失败`。
- 点击 `失败` 筛选后能看到该任务。
- 卡片下方错误信息显示类似 `target returned HTTP 500` 或 `network error: ...`。

状态含义：

| 后端状态 | 页面显示 | 说明 |
| --- | --- | --- |
| `queued` | `等待投递` | 已接收，等待后台 Worker 投递 |
| `delivering` | `投递中` | 后台 Worker 正在发送 HTTP 请求 |
| `waiting_retry` | `等待重试` | 本次投递失败，等待下一次自动重试 |
| `succeeded` | `投递成功` | 目标地址返回 2xx |
| `failed` | `最终失败` | 达到最大投递次数仍失败 |
| `dead_letter` | `人工死信` | 任务已由人工接管，不再自动投递，也不会被普通批量重试选中 |

## 4. 成功测试用例

### TC-S01 CRM 付款成功通知

目的：验证订阅付款成功后可以通知 CRM。

操作步骤：

1. 打开 `http://127.0.0.1:8001/`。
2. 点击 `成功示例`。
3. 确认目标地址为 `http://127.0.0.1:8001/mock/vendor/crm`。
4. 点击 `提交通知`。
5. 等待 1 到 3 秒，或点击 `刷新`。
6. 点击状态筛选 `成功`。

预期结果：

- 新任务状态显示 `投递成功`。
- `尝试` 显示为 `1/5`。
- 错误信息为空。

### TC-S02 广告注册成功通知

目的：验证不同业务事件可以使用同一个通知服务投递。

表单填写：

| 字段 | 值 |
| --- | --- |
| 事件类型 | `user.registered` |
| 来源系统 | `user-service` |
| 目标地址 | `http://127.0.0.1:8001/mock/vendor/ad-system` |
| HTTP Method | `POST` |
| 最大尝试次数 | `3` |
| Header JSON | `{"X-Vendor-Token":"ad-demo-token"}` |
| Body JSON | `{"userId":"U-10001","channel":"ad-google","registeredAt":"2026-06-08T14:00:00Z"}` |

操作步骤：

1. 手动填写上面的表单。
2. 点击 `提交通知`。
3. 点击 `成功` 筛选。

预期结果：

- 任务状态显示 `投递成功`。
- `尝试` 显示为 `1/3`。

### TC-S03 库存变更 PATCH 通知

目的：验证服务支持不同 HTTP Method。

表单填写：

| 字段 | 值 |
| --- | --- |
| 事件类型 | `inventory.changed` |
| 来源系统 | `order-service` |
| 目标地址 | `http://127.0.0.1:8001/mock/vendor/inventory` |
| HTTP Method | `PATCH` |
| 最大尝试次数 | `5` |
| Header JSON | `{"X-Vendor-Token":"inventory-demo-token"}` |
| Body JSON | `{"sku":"SKU-2026","delta":-1,"orderId":"O-90001"}` |

操作步骤：

1. 手动填写上面的表单。
2. 将 `HTTP Method` 选择为 `PATCH`。
3. 点击 `提交通知`。
4. 点击 `成功` 筛选。

预期结果：

- 任务状态显示 `投递成功`。
- 目标地址返回 2xx，服务记录投递成功。

## 5. 失败测试用例

### TC-F01 供应商返回 HTTP 500

目的：验证供应商服务异常时，通知服务会重试并最终记录失败。

操作步骤：

1. 打开 `http://127.0.0.1:8001/`。
2. 点击 `失败示例`。
3. 确认目标地址为 `http://127.0.0.1:8001/mock/vendor/crm?fail=1`。
4. 将 `最大尝试次数` 改为 `2`，方便快速看到最终失败。
5. 点击 `提交通知`。
6. 等待约 6 到 10 秒，或多次点击 `刷新`。
7. 点击 `失败` 筛选。

预期结果：

- 第一次失败后，任务可能短暂显示 `等待重试`。
- 第二次失败后，任务状态显示 `最终失败`。
- 错误信息显示 `target returned HTTP 500`。
- `尝试` 显示为 `2/2`。

### TC-F02 目标地址不可达

目的：验证目标系统无法连接时，通知服务会记录网络错误。

表单填写：

| 字段 | 值 |
| --- | --- |
| 事件类型 | `vendor.unreachable` |
| 来源系统 | `debug-service` |
| 目标地址 | `http://127.0.0.1:65534/mock/vendor/crm` |
| HTTP Method | `POST` |
| 最大尝试次数 | `2` |
| Header JSON | `{"X-Vendor-Token":"demo-token"}` |
| Body JSON | `{"message":"unreachable target test"}` |

操作步骤：

1. 手动填写上面的表单。
2. 点击 `提交通知`。
3. 等待约 6 到 10 秒，或多次点击 `刷新`。
4. 点击 `失败` 筛选。

预期结果：

- 任务最终状态显示 `最终失败`。
- 错误信息显示 `network error: ...`。
- `尝试` 显示为 `2/2`。

### TC-F03 非法目标地址

目的：验证提交参数校验，避免无效 URL 入队。

表单填写：

| 字段 | 值 |
| --- | --- |
| 目标地址 | `crm.internal/path` |
| Header JSON | `{}` |
| Body JSON | `{"message":"bad url test"}` |

操作步骤：

1. 将目标地址改为 `crm.internal/path`。
2. 点击 `提交通知`。

预期结果：

- 页面提示 `targetUrl must be an absolute http(s) URL`。
- 任务列表中不会新增该任务。

### TC-F04 Header JSON 格式错误

目的：验证页面能拦截错误 JSON。

表单填写：

| 字段 | 值 |
| --- | --- |
| Header JSON | `{"X-Vendor-Token":` |
| Body JSON | `{"message":"bad header json test"}` |

操作步骤：

1. 将 `Header JSON` 改为上面的非法 JSON。
2. 点击 `提交通知`。

预期结果：

- 页面提示 `JSON 格式错误`。
- 请求不会提交到后端。
- 任务列表中不会新增该任务。

## 6. API 辅助验证

页面调试之外，也可以用 API 查看状态。

查看全部任务：

```bash
curl http://127.0.0.1:8001/api/notifications
```

查看单个任务：

```bash
curl http://127.0.0.1:8001/api/notifications/{id}
```

手动重试失败任务：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/{id}/retry
```

标记失败任务为死信：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/{id}/dead-letter \
  -H 'Content-Type: application/json' \
  -d '{"actionBy":"ops-user@example.com","resolutionNote":"供应商接口已下线，转人工处理"}'
```

批量重试失败任务：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/retry
```

判断成功时，接口返回中的 `status` 应为 `succeeded`。

判断最终失败时，接口返回中的 `status` 应为 `failed`，并且 `lastError` 有错误原因。

### TC-H01 /health schema 验证

目的：验证健康检查接口能同时反映服务、数据库、Worker 和队列状态，方便页面状态条与人工排障使用。

操作步骤：

```bash
curl http://127.0.0.1:8001/health
```

预期结果：

- 返回 HTTP 200。
- 响应体 `status` 为 `ok` 或 `degraded`，不得缺失。
- `serviceVersion` 和 `schemaVersion` 为稳定字符串，用于判断前端是否请求到了预期版本的服务。
- `database.ok` 为布尔值；数据库正常时为 `true`。
- `database.path` 返回当前 SQLite 数据库路径或可读定位信息，便于确认测试用的是哪份数据。
- `worker.alive` 为布尔值；后台投递 Worker 正常运行时为 `true`。
- 如后端实现 Worker 并发字段，`worker.concurrency`、`worker.threadCount`、`worker.aliveCount` 应为非负整数；字段缺失时前端和人工排障应按单 Worker 旧版本兼容。
- `queue.counts` 返回各任务状态数量，至少覆盖 `queued`、`delivering`、`waiting_retry`、`succeeded`、`failed`、`dead_letter`。
- `queue.readyCount` 为当前可被 Worker 领取的任务数量，应与 `queued` 以及已到期的 `waiting_retry` 语义一致。
- `now` 为服务端当前时间戳，格式应稳定可解析，用于判断重试时间和 Worker tick 是否停滞。

### TC-H02 health degraded / 数据库异常

目的：验证数据库不可用或状态异常时，健康检查能降级而不是白屏或返回误导性的 `ok`。

测试思路：

1. 使用临时数据目录或只读数据库路径启动服务，模拟数据库初始化或写入失败。
2. 如实现支持环境变量配置数据库路径，可把路径指向不存在且不可创建的目录。
3. 调用 `/health`，同时观察服务端日志。
4. 恢复数据库路径后重启服务，再次调用 `/health`。

预期结果：

- 数据库异常时 `/health` 不应抛出未处理异常或返回 HTML 错误页。
- `status` 应为 `degraded`，或按实现约定返回明确的非健康状态。
- `database.ok` 应为 `false`，并提供可定位问题的错误信息或路径信息。
- `worker.alive`、`queue.counts`、`queue.readyCount` 在数据库不可读时可以为空、0 或带错误兜底，但字段结构不应整体缺失。
- 数据库恢复后 `/health` 回到 `status=ok`，`database.ok=true`。

### TC-H03 Worker 状态字段

目的：验证 `/health` 中 Worker 观测字段能帮助判断“服务活着但不投递”的问题。

操作步骤：

1. 启动服务并调用 `/health`，记录 `worker` 对象。
2. 提交一个成功任务，等待 1 到 3 秒。
3. 再次调用 `/health`。
4. 提交一个会失败的任务，例如 `/mock/vendor/crm?fail=1`，等待至少一次投递失败后再次调用 `/health`。

预期结果：

- `worker.alive=true`。
- 如配置了 `NOTIFICATION_WORKER_CONCURRENCY`，`worker.concurrency` 应等于解析后的目标并发数；`worker.threadCount` 和 `worker.aliveCount` 应能反映线程创建和存活数量。
- 多 Worker 场景下，如果 `worker.aliveCount < worker.concurrency`，页面或人工检查应能识别为部分 Worker 异常，而不是简单显示整体健康。
- `worker.startedAt` 或等价启动时间字段存在，能判断 Worker 本轮启动时间。
- `worker.lastTickAt` 或等价最近 tick 字段会随 Worker 循环更新，不应长期停在启动时间。
- `worker.lastClaimedJobId` 或等价最近 claimed job 字段在 Worker 领取任务后更新，可用于关联任务详情。
- `worker.lastError` 或等价最近错误字段在失败投递后能记录最近 Worker 错误；无错误时为空、`null` 或明确的空值。
- 页面状态条展示的 Worker 文案应与这些字段一致，不能只根据首页加载成功判断 Worker 健康。

### TC-H03A Worker 并发与不重复领取

目的：验证 `NOTIFICATION_WORKER_CONCURRENCY` 的配置、`/health` 并发字段，以及多个 Worker 同时运行时不会重复领取同一条任务。

建议配置：

```bash
NOTIFICATION_WORKER_CONCURRENCY=3 \
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=2 \
python3 server.py --host 127.0.0.1 --port 8001
```

测试思路：

1. 使用独立测试数据库启动服务，避免历史任务干扰。
2. 调用 `/health`，记录 `worker.concurrency`、`worker.threadCount`、`worker.aliveCount`、`worker.alive` 和 `queue.readyCount`。
3. 快速提交 10 到 20 条目标为慢速 mock vendor 的任务，例如 `delayMs=1000`，让多个 Worker 有机会同时领取。
4. 持续轮询 `/api/notifications`、`/api/notifications/{id}/attempts` 和 `/health`，直到任务进入 `succeeded`、`waiting_retry` 或 `failed`。
5. 检查数据库或 attempts 响应中每条任务的领取和投递记录，确认同一投递轮次没有被多个 Worker 同时写出重复的真实 HTTP attempt。
6. 将 `NOTIFICATION_WORKER_CONCURRENCY` 改回 `1` 重启，重复提交少量任务，确认 health 字段和投递语义仍兼容。

预期结果：

- `worker.concurrency` 等于配置解析后的目标值；非法值按后端约定回退、clamp 或启动失败并给出明确错误。
- `worker.threadCount` 不小于已启动的 Worker 线程数量，`worker.aliveCount` 不大于 `threadCount`，且正常启动后应等于或接近 `concurrency`。
- 多 Worker 同时处理积压任务时，同一任务在同一 `deliveryRun` / `attemptNumber` 下不应出现重复领取、重复状态覆盖或重复成功记录。
- `queue.readyCount` 应随着任务被领取下降；如果长期不下降，应结合 `aliveCount`、`lastError`、SQLite 锁等待和供应商响应判断瓶颈。
- 调大并发不应破坏 `requestId` 幂等、attempts 历史递增、手动重试、租约回收和最终失败状态。
- 如果当前后端尚未实现并发字段，测试结论应明确记录“按单 Worker 旧版本兼容”，并不得让前端因为字段缺失白屏。

### TC-H04 重启恢复 delivering 任务

目的：验证服务重启时正在投递的任务不会永久卡在 `delivering`，并能记录重启中断原因。

操作步骤：

1. 使用慢速 mock vendor 创建任务，例如目标地址包含 `delayMs=10000`，确保任务进入 `delivering`。
2. 在任务仍为 `delivering` 时停止服务进程。
3. 重新启动服务。
4. 调用 `/api/notifications/{id}` 和 `/health`。
5. 等待 Worker 再次领取任务并完成投递或进入失败/重试。

预期结果：

- 重启后原 `delivering` 任务应回到 `queued` 或其他可再次投递状态，不应永久保持 `delivering`。
- 任务 `lastError` 应说明上一次投递被服务重启中断，例如包含“restart interrupted delivery”或同等语义。
- `/health` 中 `queue.readyCount` 应包含该恢复后的可投递任务。
- Worker 再次领取后，`worker.lastClaimedJobId` 可指向该任务。
- attempts 历史不应被清空；如重启前未形成完整 attempt，应按实现约定记录中断或保持审计一致。

### TC-H05 stuck delivering 租约回收

目的：验证 Worker 进程未退出但任务长时间卡在 `delivering` 时，系统可以通过租约超时把任务回收到可投递状态。当前实现已支持不依赖重启的租约回收，并会记录 `lease_timeout` attempt。

建议配置：

```bash
NOTIFICATION_DELIVERING_LEASE_SECONDS=3 \
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=1 \
python3 server.py --host 127.0.0.1 --port 8001
```

测试构造方式：

1. 使用测试数据库启动服务，确保环境干净。
2. 创建一个慢速或可阻塞的目标任务，让任务进入 `delivering`。
3. 在不重启服务的情况下，模拟 Worker 无法完成该任务。可选方式包括使用测试替身阻塞投递线程，或在数据库中准备一条 `status=delivering` 且 `updated_at` / `delivery_started_at` 已早于租约时间的任务。
4. 等待超过 `NOTIFICATION_DELIVERING_LEASE_SECONDS`。
5. 调用 `/api/notifications/{id}` 和 `/health`。
6. 继续等待 Worker 再次领取该任务，或手动刷新列表观察状态变化。

预期结果：

- 超过租约时间后，任务不应永久停留在 `delivering`。
- 任务应回到 `queued`、`waiting_retry` 或其他可再次投递状态，具体状态按后端实现约定。
- `lastError` 应包含 `lease expired`、`delivery lease timeout` 或同等语义，便于区分普通供应商失败。
- `/health` 中的 `queue.readyCount` 应能反映恢复后的可领取任务数量。
- attempts 历史不应被删除；如果回收动作本身会写审计记录，应能看出它不是一次真实供应商 HTTP 投递。
- 租约时间必须大于单次 HTTP 投递超时时间，避免正常慢请求被过早回收并重复投递。

### TC-H06 health / stats 运维判断

目的：验证运维人员可以通过 `/health` 和 `/api/stats` 判断投递是否成功、是否积压、是否发生 lease 回收和失败趋势。

操作步骤：

1. 调用 `/health`，记录 `database`、`worker`、`queue` 和 `now`。
2. 提交一个成功任务，等待进入 `succeeded`，再次调用 `/health`。
3. 提交一个失败任务，等待进入 `waiting_retry` 或 `failed`，再次调用 `/health`。
4. 构造或等待一次 `delivering` 租约回收，记录 `/health` 中 lease 相关字段。
5. 调用 `/api/stats`：

```bash
curl http://127.0.0.1:8001/api/stats
```

预期结果：

- 判断数据库：`database.ok=true` 表示数据库可查询；为 `false` 或 `/health` 返回 `degraded` 时，应优先排查数据库路径和权限。
- 判断 Worker：`worker.alive=true` 且 `worker.lastTickAt` 持续更新，说明后台投递循环仍在工作；如果首页能打开但 `worker.alive=false`，不能认为系统整体健康。
- 判断投递成功：单任务是否成功必须以 `/api/notifications/{id}` 响应中的 `status=succeeded` 或页面任务状态为准；`queue.counts.succeeded` 只能看总体数量变化。
- 判断积压：`queue.readyCount` 长时间大于 0，或 `queue.counts.queued` 持续增长，说明可投递任务没有被及时领取。
- 判断供应商失败趋势：`queue.counts.waiting_retry` 持续增长通常表示供应商短期失败或限流；`queue.counts.failed` 持续增长表示自动重试耗尽，需要人工补偿或供应商排障。
- 判断人工接管趋势：`queue.counts.dead_letter` 持续增长表示越来越多任务被人工标记，需要排查供应商长期不可用、配置错误或缺少后续工单流程。
- 判断 lease 回收：`queue.expiredDeliveringCount` 大于 0 表示当前仍有超出租约的投递中任务；`worker.lastLeaseRecoveryCount` 增加、`worker.lastLeaseRecoveryAt` 更新，表示本轮 Worker 已执行过回收。
- `/api/stats` 应返回 `status=ok`、`serviceVersion`、`schemaVersion`、`queue.counts`、`queue.readyCount`、`queue.expiredDeliveringCount`、`notifications.total`、`notifications.averageAttempts`、`attempts.total`、`attempts.recentErrorCount`、`attempts.recentErrorsByType` 和 `now`；这些字段应与 `/health`、列表 API 和单任务详情口径一致。
- `/api/stats` 不应替代 attempts：定位某个任务为什么失败时，仍必须查看 `/api/notifications/{id}/attempts`。

### TC-H07 health / stats 版本字段错配排查

目的：验证 `/health` 和 `/api/stats` 暴露的版本字段能帮助定位“旧服务/新前端”错配，但不会被误当成完整迁移系统。

操作步骤：

1. 调用 `/health`：

```bash
curl http://127.0.0.1:8001/health
```

2. 调用 `/api/stats`：

```bash
curl http://127.0.0.1:8001/api/stats
```

3. 打开首页，观察 Health/Worker 状态小条或等价区域是否展示或至少安全消费版本字段。
4. 如有条件，使用旧前端连接新服务或新前端连接旧服务，观察页面错误提示和控制台日志。

预期结果：

- `/health` 和 `/api/stats` 都返回 `serviceVersion` 和 `schemaVersion`，且类型为字符串。
- 两个接口中的版本字段口径一致；如果暂时不一致，页面应提示错配或降级显示，不应白屏。
- 版本字段仅用于人工排查和前端兼容判断；测试不能把它当作数据库 migration 是否完成的唯一依据。
- 当前端发现缺少 `schemaVersion` 或版本不符合预期时，应展示可理解的错误或降级提示，不能因为字段缺失触发未处理 JS 异常。

### TC-H08 Prometheus 指标草案验收

目的：验证文档中已经明确 Prometheus 文本指标是演进草案，当前代码不要求实现 `/metrics`，同时确认未来指标可以从 `/health` 和 `/api/stats` 映射。

操作步骤：

1. 阅读 `docs/api-contract.md` 的 Prometheus 文本指标草案。
2. 调用 `/health`：

```bash
curl http://127.0.0.1:8001/health
```

3. 调用 `/api/stats`：

```bash
curl http://127.0.0.1:8001/api/stats
```

4. 对照草案中的指标名和来源字段。
5. 可选：访问 `/metrics`，确认当前版本没有把它作为必须接口依赖。

预期结果：

- 草案明确说明当前代码不实现 `/metrics`，不引入 Prometheus 客户端依赖。
- `notification_database_up` 可由 `/health.database.ok` 映射。
- `notification_worker_alive` 可由 `/health.worker.alive` 映射。
- `notification_queue_status_total{status}` 可由 `/health.queue.counts` 或 `/api/stats.queue.counts` 映射。
- `notification_queue_ready_total` 可由 `queue.readyCount` 映射。
- `notification_attempts_recent_errors_by_type_total{error_type}` 可由 `/api/stats.attempts.recentErrorsByType` 映射。
- 草案应说明不把 `requestId`、`targetUrl`、用户 ID 或原始错误文本作为 Prometheus 标签，避免高基数和敏感信息泄露。
- 如果当前 `/metrics` 返回 404，不视为失败；只有未来代码明确实现 `/metrics` 后，才需要增加 Prometheus 文本格式、Content-Type 和 scrape 兼容性测试。

### TC-R01 批量重试 failed 任务

目的：验证批量重试 API 只对最终失败任务重新入队，并返回本次处理明细。

前置条件：

- 至少创建 2 个 `failed` 任务。
- 同时保留若干 `queued`、`delivering`、`waiting_retry` 或 `succeeded` 任务，用于验证非允许状态不会被批量重试。

操作步骤：

1. 记录待重试 failed 任务的 `id`、`deliveryRun`、`attemptCount` 和 attempts 条数。
2. 调用：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/retry
```

3. 再调用 `/api/notifications` 和 `/api/notifications/{id}` 查看返回任务状态。
4. 对每个被重试任务调用 `/api/notifications/{id}/attempts`。

预期结果：

- 返回 HTTP 200。
- 响应体包含 `count` 和 `items`。
- `count` 等于本次实际重新入队的 failed 任务数量。
- `items` 中每个任务状态为 `queued`。
- 每个被重试任务的 `deliveryRun` 比重试前增加 `1`。
- 当前轮次的 `attemptCount` 重置为从 0 重新计算。
- 历史 attempts 保留，不会因为批量重试被删除。
- 非 failed 状态任务不会出现在 `items` 中，也不会被改成 `queued`。

### TC-R02 批量重试边界

操作步骤：

1. 在没有 `failed` 任务时调用 `POST /api/notifications/retry`。
2. 构造超过接口默认数量的 failed 任务后调用：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/retry \
  -H 'Content-Type: application/json' \
  -d '{"limit":2}'
```

3. 构造 `queued`、`delivering`、`waiting_retry`、`succeeded` 任务后再次调用批量重试。

预期结果：

- 无 failed 任务时返回 HTTP 200，`count` 为 `0`，`items` 为空数组。
- 设置 `limit=2` 时，最多只返回和重试 2 个 failed 任务。
- `limit` 不应允许绕过服务端最大批量限制。
- 非允许状态任务不被批量重试；如果接口支持显式传状态过滤，非 failed 状态应返回 0 项或明确错误，不应静默修改任务。
- 重复调用批量重试不会重复增加已变为 `queued` 的任务 `deliveryRun`。

### TC-DL01 失败任务标记死信

目的：验证已经自动重试耗尽的失败任务可以被人工标记为 `dead_letter`，并记录最近一次人工动作字段。

前置条件：

- 创建一个 `maxAttempts=1` 或 `maxAttempts=2` 的失败任务，并等待状态进入 `failed`。
- 记录任务 `id`、`status`、`deliveryRun`、`attemptCount` 和 attempts 条数。

操作步骤：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/{id}/dead-letter \
  -H 'Content-Type: application/json' \
  -d '{
    "actionBy": "ops-user@example.com",
    "resolutionNote": "供应商接口已下线，转人工确认"
  }'
```

随后查询详情：

```bash
curl http://127.0.0.1:8001/api/notifications/{id}
```

预期结果：

- `POST /dead-letter` 返回 HTTP 200。
- 任务 `status` 变为 `dead_letter`。
- `lastManualAction=dead_letter`。
- `lastManualActionAt` 存在且为可解析时间。
- `lastManualActionBy=ops-user@example.com`。
- `resolutionNote` 返回本次提交的人工处置说明。
- `deliveryRun` 不应因为标记死信而增加。
- attempts 历史不应被清空，也不应伪造一次供应商 HTTP 投递。
- 后续 Worker 不应自动领取该任务。

### TC-DL02 投递中任务不能标记死信

目的：验证正在发送 HTTP 请求的 `delivering` 任务不能被人工直接改成死信，避免真实投递仍在进行时产生状态冲突。

操作步骤：

1. 使用慢速 mock vendor 创建任务，例如 `targetUrl=http://127.0.0.1:8001/mock/vendor/crm?delayMs=10000`。
2. 刷新列表或查询详情，确认任务进入 `delivering`。
3. 在任务仍为 `delivering` 时调用：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications/{id}/dead-letter \
  -H 'Content-Type: application/json' \
  -d '{"actionBy":"ops-user@example.com","resolutionNote":"尝试在投递中标记死信"}'
```

4. 再次查询任务详情。

预期结果：

- 接口返回 HTTP 409 或明确的冲突错误。
- 任务状态不应被改成 `dead_letter`。
- `lastManualAction`、`lastManualActionAt`、`lastManualActionBy`、`resolutionNote` 不应被本次失败操作覆盖。
- 原投递流程继续按超时、成功、失败或租约回收策略处理。

### TC-DL03 死信不会被普通批量重试误操作

目的：验证 `dead_letter` 任务已经进入人工接管状态，不会被默认批量重试或普通 failed 批量重试重新入队。

前置条件：

- 至少有 1 个 `dead_letter` 任务。
- 至少有 1 个普通 `failed` 任务，用于确认批量重试仍能处理非死信失败任务。

操作步骤：

1. 记录死信任务的 `id`、`deliveryRun`、`attemptCount`、`lastManualAction` 和 `resolutionNote`。
2. 调用默认批量重试：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/retry
```

3. 如接口支持请求体状态过滤，再调用：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/retry \
  -H 'Content-Type: application/json' \
  -d '{"status":"failed","limit":50}'
```

4. 再次查询死信任务详情。

预期结果：

- 批量重试响应中的 `items` 不包含死信任务 `id`。
- 死信任务状态仍为 `dead_letter`。
- 死信任务 `deliveryRun`、`attemptCount` 不因普通批量重试变化。
- `lastManualAction` 和 `resolutionNote` 保持不变。
- 普通 `failed` 任务仍可按 TC-R01 被重新入队，说明接口不是整体失效。

### TC-DL04 单条 retry 可以从死信重新入队

目的：验证只有明确指定任务 ID 的人工 retry 才能把 `dead_letter` 任务恢复到可投递状态。

前置条件：

- 准备一个 `dead_letter` 任务，并记录其 `deliveryRun`、`attemptCount` 和 attempts 条数。

操作步骤：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/{id}/retry \
  -H 'Content-Type: application/json' \
  -d '{
    "actionBy": "ops-user@example.com",
    "resolutionNote": "供应商已恢复，人工从死信重新入队"
  }'
```

随后查询详情和 attempts：

```bash
curl http://127.0.0.1:8001/api/notifications/{id}
curl http://127.0.0.1:8001/api/notifications/{id}/attempts
```

预期结果：

- `POST /retry` 返回 HTTP 200。
- 任务状态变为 `queued`，等待 Worker 重新投递。
- `deliveryRun` 比重试前增加 `1`。
- 当前轮次 `attemptCount` 重置为 0，后续由 Worker 重新累加。
- 历史 attempts 保留，新的投递尝试追加到旧记录之后。
- `lastManualAction` 更新为 `retry` 或等价人工重试动作。
- `lastManualActionBy` 和 `resolutionNote` 能反映本次从死信恢复的操作者和原因。

### TC-DL05 页面展示人工动作字段

目的：验证白屏调试页面能让人工处理状态可见，避免只看到 `dead_letter` 却不知道是谁、何时、为什么处置。

操作步骤：

1. 按 TC-DL01 创建一个死信任务。
2. 打开 `http://127.0.0.1:8001/`。
3. 在列表中找到该任务，或使用状态/关键词过滤定位。
4. 打开任务详情。
5. 观察状态、人工动作字段和 attempts 区域。

预期结果：

- 列表或详情中能看到任务状态为 `人工死信` 或 `dead_letter`。
- 详情展示 `lastManualAction`、`lastManualActionAt`、`lastManualActionBy`、`resolutionNote`。
- `resolutionNote` 长文本应自动换行，不应撑破弹窗或导致页面白屏。
- attempts 历史仍可展开查看，人工标记不应覆盖真实投递失败记录。
- 如果页面提供 `重新投递` 按钮，死信任务点击后应按 TC-DL04 重新入队；如果页面提供 `标记死信` 按钮，非 failed 状态应禁用或提示不可操作。

### TC-L01 列表组合过滤

目的：验证 `/api/notifications` 支持按 `status`、`eventType`、`sourceSystem`、`targetUrl` 和 `limit` 组合查询，且过滤条件之间为 AND 关系。

前置数据建议：

| eventType | sourceSystem | targetUrl | 预期状态 |
| --- | --- | --- | --- |
| `payment.succeeded` | `billing-service` | `/mock/vendor/crm` | `succeeded` |
| `payment.failed` | `billing-service` | `/mock/vendor/crm?status=500` | `failed` |
| `user.registered` | `user-service` | `/mock/vendor/crm` | `succeeded` |
| `ad.lead.created` | `ads-service` | `/mock/vendor/crm?status=500` | `failed` |

操作步骤：

1. 单独按状态过滤：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed"
```

2. 组合 `status + eventType`：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed&eventType=payment.failed"
```

3. 组合 `status + sourceSystem`：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=succeeded&sourceSystem=user-service"
```

4. 组合 `eventType + sourceSystem + targetUrl`，`targetUrl` 需 URL encode：

```bash
curl "http://127.0.0.1:8001/api/notifications?eventType=payment.succeeded&sourceSystem=billing-service&targetUrl=http%3A%2F%2F127.0.0.1%3A8001%2Fmock%2Fvendor%2Fcrm"
```

5. 在组合条件后追加 `limit=1`：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed&sourceSystem=billing-service&limit=1"
```

预期结果：

- 返回 HTTP 200。
- 每条返回任务都同时满足请求中的全部过滤条件。
- `targetUrl` 使用关键词匹配，可以用供应商路径、host 或 query 片段定位任务。
- `limit=1` 时 `items` 最多 1 条，且仍满足其他过滤条件。
- 空结果返回空数组，不应返回全部列表或报错。
- 非法 `limit`、未知 `status` 或过大的 `limit` 应按接口约定返回错误或被安全 clamp，不应导致 500。

### TC-L02 列表分页

目的：验证 `/api/notifications` 的 `limit`、`offset` 和 `pagination` 响应能支持页面翻页，且不会丢失过滤条件。

前置数据建议：至少创建 6 条不同 `eventType` 或 `sourceSystem` 的任务，其中包含成功和失败状态。

操作步骤：

1. 请求第一页：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=2&offset=0"
```

2. 请求第二页：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=2&offset=2"
```

3. 请求带过滤条件的分页：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed&limit=2&offset=0"
```

4. 使用第一页响应中的 `pagination.offset + pagination.limit` 请求下一页。
5. 构造非法 `offset`：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=2&offset=abc"
```

预期结果：

- 正常请求返回 HTTP 200，响应体包含 `items` 和 `pagination`。
- `pagination.limit`、`pagination.offset`、`pagination.count`、`pagination.hasMore`、`pagination.sort`、`pagination.order` 与本次查询一致。
- `items.length` 不超过 `limit`，`pagination.count` 等于本次实际返回数量。
- 第一页和第二页在稳定排序下不应重复同一条任务。
- 带过滤条件翻页时，下一页仍只返回满足过滤条件的任务。
- 非法 `offset` 返回 HTTP 400 或明确参数错误，不应返回 500。

### TC-L03 列表排序

目的：验证 `/api/notifications` 的 `sort` 和 `order` 能稳定排序，并且非法排序参数不会导致服务异常。

操作步骤：

1. 按创建时间倒序：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=5&sort=createdAt&order=desc"
```

2. 按创建时间正序：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=5&sort=createdAt&order=asc"
```

3. 按状态排序后再分页：

```bash
curl "http://127.0.0.1:8001/api/notifications?limit=3&offset=0&sort=status&order=asc"
```

4. 构造非法排序字段：

```bash
curl "http://127.0.0.1:8001/api/notifications?sort=body&order=desc"
```

5. 构造非法排序方向：

```bash
curl "http://127.0.0.1:8001/api/notifications?sort=createdAt&order=random"
```

预期结果：

- 合法排序返回 HTTP 200。
- `pagination.sort` 和 `pagination.order` 与请求一致；未传时使用默认排序。
- `createdAt desc` 的第一页应优先看到最新任务，`createdAt asc` 应优先看到最早任务。
- 同一排序字段值相同时，结果顺序仍应稳定，不应刷新一次顺序就随机变化。
- 非法 `sort` 或 `order` 返回 HTTP 400 或明确参数错误，不应执行任意字段排序，也不应返回 500。

### TC-L04 导出 CSV

目的：验证 `/api/notifications/export.csv` 能按当前过滤、分页和排序条件导出 CSV，且不泄露 Header 或 Body 敏感原文。

操作步骤：

1. 创建一条带敏感 Header 和 Body 的任务，例如 Header 包含 `Authorization`，Body 包含 `password` 或 `secret`。
2. 请求失败任务导出：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?status=failed&limit=20&sort=createdAt&order=desc"
```

3. 请求分页导出：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?limit=2&offset=2"
```

4. 请求空结果导出：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?eventType=not-exists"
```

5. 构造非法参数：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?sort=headers"
```

预期结果：

- 成功导出返回 HTTP 200，`Content-Type` 为 `text/csv` 或 `text/csv; charset=utf-8`。
- 建议返回 `Content-Disposition: attachment; filename="notifications.csv"`，便于浏览器下载。
- CSV 包含 `id`、`requestId`、`eventType`、`sourceSystem`、`targetUrl`、`status`、`failureType`、`attemptCount`、`deliveryRun`、`lastStatusCode`、`createdAt`、`updatedAt`、`deliveredAt`、`lastError`。
- CSV 不包含 `headers` 列，不包含完整 `body` 原文或 `bodyPreview`。
- `lastError` 和可疑 URL query 中的 token、authorization、secret、password、key 等敏感值必须是脱敏后的展示值。
- 空结果导出返回只有表头的 CSV，而不是 JSON 错误或空响应。
- 导出的行数应受 `limit/offset`、过滤条件和排序参数影响，和列表 API 的同条件结果一致。
- 非法参数返回 HTTP 400 或明确参数错误，不应下载包含敏感数据的文件，也不应返回 500。

### TC-L05 列表时间范围过滤

目的：验证 `/api/notifications` 支持按 `createdAt` 和 `updatedAt` 时间范围过滤，且时间范围与状态、事件类型等条件之间为 AND 关系。

前置数据建议：

- 至少创建 3 条任务，记录每条任务的 `createdAt` 和 `updatedAt`。
- 让其中 1 条任务发生状态变化，例如从 `queued` 进入 `succeeded` 或 `failed`，确保 `updatedAt` 与 `createdAt` 可区分。

操作步骤：

1. 按创建时间范围查询：

```bash
curl "http://127.0.0.1:8001/api/notifications?createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&sort=createdAt&order=asc"
```

2. 按更新时间范围查询：

```bash
curl "http://127.0.0.1:8001/api/notifications?updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

3. 组合状态、来源系统和时间范围：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed&sourceSystem=billing-service&createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z"
```

4. 构造非法时间：

```bash
curl -i "http://127.0.0.1:8001/api/notifications?createdFrom=not-a-time"
```

5. 构造反向范围：

```bash
curl -i "http://127.0.0.1:8001/api/notifications?updatedFrom=2026-06-10T00:00:00Z&updatedTo=2026-06-09T00:00:00Z"
```

预期结果：

- 合法请求返回 HTTP 200，响应体包含 `items` 和 `pagination`。
- `createdFrom/createdTo` 对 `createdAt` 生效，`updatedFrom/updatedTo` 对 `updatedAt` 生效，端点按闭区间判断。
- 同时传入多个过滤条件时，每条返回任务都满足全部条件。
- 空时间窗口返回 `items=[]`，不能回退成无过滤列表。
- 非法时间或反向范围返回 HTTP 400 或明确参数错误，不应返回 500。
- `pagination.sort`、`pagination.order`、`pagination.limit`、`pagination.offset` 仍与请求一致，时间过滤不应破坏分页和排序。

### TC-L06 CSV 时间范围导出

目的：验证 `/api/notifications/export.csv` 复用列表 API 的时间范围过滤，导出内容与同条件列表结果一致。

操作步骤：

1. 先请求同条件列表，记录返回任务 ID：

```bash
curl "http://127.0.0.1:8001/api/notifications?status=failed&createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

2. 使用同一组条件导出 CSV：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?status=failed&createdFrom=2026-06-09T00:00:00Z&createdTo=2026-06-10T00:00:00Z&updatedFrom=2026-06-09T00:00:00Z&updatedTo=2026-06-10T00:00:00Z&sort=updatedAt&order=desc"
```

3. 构造非法时间导出：

```bash
curl -i "http://127.0.0.1:8001/api/notifications/export.csv?updatedTo=not-a-time"
```

预期结果：

- 合法导出返回 HTTP 200，`Content-Type` 为 `text/csv` 或 `text/csv; charset=utf-8`。
- CSV 行中的 `createdAt` 和 `updatedAt` 都满足请求时间范围。
- CSV 中的任务 ID 与同条件列表结果一致，排序方向一致。
- 空结果导出只有表头，不应下载全部历史任务。
- 非法时间返回 HTTP 400 或明确参数错误，不应返回 500，也不应下载含数据的 CSV。
- CSV 仍不包含 `headers`、完整 `body` 或 `bodyPreview`。

## 7. Attempts API 验证矩阵

Attempts API 用于验收每一次真实投递尝试的历史记录，特别适合长周期迭代后确认“任务当前状态”和“历史投递事实”没有被混在一起。

基础接口：

```bash
curl http://127.0.0.1:8001/api/notifications/{id}/attempts
```

### TC-A01 成功任务只有 1 条成功尝试

操作步骤：

1. 点击页面 `成功示例`。
2. 点击 `提交通知`。
3. 等待任务状态变为 `投递成功`。
4. 调用 `/api/notifications/{id}/attempts`。

预期结果：

- 接口返回 HTTP 200。
- `items` 为数组，至少包含 1 条记录。
- 最后一条记录 `attemptNumber` 为 `1`。
- 最后一条记录 `status` 为 `succeeded`。
- 最后一条记录 `statusCode` 为 `200`。
- `durationMs` 是非负整数。
- 单任务详情弹窗中的“投递尝试”区域显示同一条成功尝试。

### TC-A02 失败任务保留多条失败尝试

操作步骤：

1. 点击页面 `失败示例`。
2. 将 `最大尝试次数` 改为 `2`。
3. 点击 `提交通知`。
4. 等待任务状态变为 `最终失败`。
5. 调用 `/api/notifications/{id}/attempts`。

预期结果：

- 接口返回 HTTP 200。
- `items` 至少包含 2 条记录。
- 最近 2 条记录的 `attemptNumber` 依次为 `1`、`2`。
- 最近 2 条记录的 `status` 均为 `failed`。
- 最近 2 条记录的 `statusCode` 均为 `500`。
- 每条失败尝试都有 `createdAt` 和 `durationMs`。
- `error` 显示 `target returned HTTP 500`，且不包含敏感 Header 或 Body 明文。

### TC-A03 不存在任务返回 404

操作步骤：

```bash
curl -i http://127.0.0.1:8001/api/notifications/not-exist/attempts
```

预期结果：

- 返回 HTTP 404。
- 响应体包含 `notification not found`。
- 页面打开不存在任务详情时应显示加载失败提示，不应白屏或卡死。

### TC-A04 手动重试后的历史记录语义

操作步骤：

1. 先按 TC-A02 创建一个 `最终失败` 任务，并记录其 attempts 条数。
2. 点击任务卡片的 `重新投递`，或调用：

```bash
curl -X POST http://127.0.0.1:8001/api/notifications/{id}/retry
```

3. 等待任务再次进入 `最终失败` 或 `投递成功`。
4. 再次调用 `/api/notifications/{id}/attempts`。

预期结果：

- 手动重试会把任务当前 `attemptCount` 重置为从 0 重新计算。
- 历史 attempts 不应被删除。
- 新一轮投递尝试应追加在旧记录之后，按 `createdAt ASC, id ASC` 顺序返回。
- `attemptNumber` 表示本轮投递次数：手动重试或批量重试后的新一轮应重新从 `1` 开始。
- `attemptSequence` 表示全历史递增序号：历史第一条为 `1`，之后每追加一条都比上一条增加 `1`，重试后不得归零或重复。
- `deliveryRun` 表示投递轮次：首次投递为 `1`，每次手动重试或批量重试后增加 `1`。
- 同一个 `deliveryRun` 内的 `attemptNumber` 从 `1` 递增；跨 `deliveryRun` 时 `attemptNumber` 可重新开始，但 `attemptSequence` 必须继续递增。
- 如果重试后仍失败，详情弹窗应同时能看到重试前和重试后的失败尝试。
- 如果重试前修改为成功目标再重试，最终任务状态可以变为 `succeeded`，但旧失败 attempts 仍保留，用于审计。

### TC-A05 attempts 全历史序号一致性

目的：单独验收 `attemptNumber`、`attemptSequence` 和 `deliveryRun` 三个字段的语义不会在自动重试、手动重试和批量重试后混淆。

操作步骤：

1. 创建一个 `maxAttempts=2` 的失败任务，等待进入 `failed`。
2. 调用 `/api/notifications/{id}/attempts`，记录前两条 attempts。
3. 对该任务执行一次手动重试，等待再次失败或成功。
4. 再次调用 attempts API。
5. 如果该任务仍为 `failed`，再通过 `POST /api/notifications/retry` 且请求体为 `{"limit":1}` 执行一次批量重试并再次查看 attempts。

预期结果：

- 首轮两条 attempts 的 `deliveryRun` 均为 `1`，`attemptNumber` 为 `1`、`2`，`attemptSequence` 为 `1`、`2`。
- 第二轮第一条 attempt 的 `deliveryRun` 为 `2`，`attemptNumber` 为 `1`，`attemptSequence` 为 `3`。
- 若第三轮由批量重试触发，第三轮第一条 attempt 的 `deliveryRun` 为 `3`，`attemptNumber` 为 `1`，`attemptSequence` 继续为上一条加 `1`。
- attempts 数组中不应出现重复或倒退的 `attemptSequence`。
- 详情弹窗展示 attempts 时，应能看出不同 `deliveryRun` 和全历史 `attemptSequence`，避免把第二轮第一条误读成全历史第一条。

也可以直接运行 smoke 测试做基础回归：

```bash
python3 scripts/smoke_test.py
```

或对已启动服务执行：

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8001
```

## 8. 目标 URL 安全 / SSRF 测试矩阵

目标 URL 校验必须保证 Demo 自带 mock vendor 可调试，同时避免把通知服务变成访问内网、云元数据或本机服务的代理。以下用例用于长周期迭代后的安全回归验收。

| 用例 | targetUrl | 预期 |
| --- | --- | --- |
| TC-U01 | `http://127.0.0.1:8001/mock/vendor/crm` | same-origin mock vendor 允许入队并可投递成功 |
| TC-U02 | `http://127.0.0.1:8001/mock/vendor/crm?fail=1` | same-origin mock vendor 允许入队，投递后按 500 重试并最终失败 |
| TC-U03 | `http://localhost:8001/mock/vendor/crm` | 如果服务当前 origin 是 `127.0.0.1:8001`，应拒绝非 same-origin localhost 写法，除非被明确归一化为 mock vendor |
| TC-U04 | `http://localhost:65534/anything` | localhost 非 mock 目标拒绝，返回 400，不入队 |
| TC-U05 | `http://127.0.0.1:65534/anything` | 127.0.0.1 非 mock 目标拒绝，返回 400，不入队 |
| TC-U06 | `http://10.0.0.1/anything` | 10.0.0.0/8 私网地址拒绝，返回 400，不入队 |
| TC-U07 | `http://172.16.0.1/anything` | 172.16.0.0/12 私网地址拒绝，返回 400，不入队 |
| TC-U08 | `http://192.168.1.1/anything` | 192.168.0.0/16 私网地址拒绝，返回 400，不入队 |
| TC-U09 | `http://169.254.169.254/latest/meta-data/` | link-local / 云元数据地址拒绝，返回 400，不入队 |
| TC-U10 | `http://[::1]:8001/anything` | IPv6 loopback 拒绝，返回 400，不入队 |
| TC-U11 | `https://api.allowed-vendor.example/webhook` | 配置在白名单 origin 中时允许入队 |
| TC-U12 | `https://api.not-allowed.example/webhook` | 不在白名单 origin 中时拒绝，返回 400，不入队 |
| TC-U13 | 白名单 origin 下 `/redirect/localhost`，302 到 `http://127.0.0.1:8001/mock/vendor/crm` | 初始 URL 可入队，但 Worker 跟随重定向前必须拒绝 localhost 目标，任务不得成功 |
| TC-U14 | 白名单 origin 下 `/redirect/private`，302 到 `http://10.0.0.1/` 或 `http://169.254.169.254/latest/meta-data/` | Worker 必须拒绝私网、link-local 或 metadata 重定向目标，且不能向该地址发起真实请求 |
| TC-U15 | 白名单 origin 下 `/ok` 直接返回 2xx | 正常 2xx 不受 redirect 安全校验影响，任务可进入 `succeeded` |

统一验收点：

- TC-U04 到 TC-U12 这类初始目标地址非法或不在白名单的用例，必须在创建任务阶段失败，不应等到 Worker 投递阶段才失败。
- TC-U13 到 TC-U15 是 redirect 二次校验用例，初始 URL 可以合法入队，但重定向后的 `Location` 必须在投递阶段重新校验。
- 拒绝响应错误信息应能说明原因，例如 `targetUrl is not allowed`，但不暴露内部网络探测细节。
- 被拒绝的 URL 不应出现在 `/api/notifications` 列表中。
- URL 校验要覆盖 hostname 解析、IPv4、IPv6、私网段、loopback、link-local 和白名单 origin。
- only mock vendor 的本机例外必须限定到当前服务 same-origin 下的 `/mock/vendor/` 路径。
- 重定向目标必须二次校验：初始 URL 合法不代表 `Location` 合法，302/307/308 跳转到 localhost、私网、link-local、metadata 或非白名单 Origin 时必须拒绝。

### TC-U13 到 TC-U15 redirect 二次校验

目的：验证供应商 URL 初始合法但 HTTP 重定向到危险地址时，通知服务不会被绕过 SSRF 边界。

测试准备：

1. 准备一个外部测试 receiver，Origin 配在 `NOTIFICATION_ALLOWED_TARGETS` 中，例如 `https://redirect-fixture.example`。
2. receiver 提供以下路径：
   - `/ok`：直接返回 HTTP 200。
   - `/redirect/localhost`：返回 302，`Location: http://127.0.0.1:8001/mock/vendor/crm`。
   - `/redirect/private`：返回 302，`Location: http://10.0.0.1/anything`。
   - `/redirect/metadata`：返回 302，`Location: http://169.254.169.254/latest/meta-data/`。
3. 如条件允许，在危险目标侧准备访问日志或防火墙记录，确认通知服务没有真正发起第二跳请求。

操作步骤：

1. 提交 `targetUrl=https://redirect-fixture.example/ok`，`maxAttempts=1`。
2. 提交 `targetUrl=https://redirect-fixture.example/redirect/localhost`，`maxAttempts=1`。
3. 提交 `targetUrl=https://redirect-fixture.example/redirect/private`，`maxAttempts=1`。
4. 提交 `targetUrl=https://redirect-fixture.example/redirect/metadata`，`maxAttempts=1`。
5. 分别查看任务详情和 attempts。

预期结果：

- `/ok` 用例正常投递成功，`status=succeeded`，attempt `statusCode=200`。
- 三个 redirect 用例可以在创建阶段通过初始 Origin 校验并入队，但投递阶段必须在跟随 `Location` 前拒绝危险目标。
- redirect 用例不得进入 `succeeded`；应进入 `failed` 或 `waiting_retry`，具体取决于 `maxAttempts`。
- `lastError` 或 attempt `error` 应明确包含 redirect 目标不安全、目标不允许或同等语义。
- 当前契约下 redirect 拦截可归类为 `delivery_error`；如果后续扩展 `security_error` 或专门的 `redirect_blocked`，必须同步更新 [api-contract.md](api-contract.md)，但无论如何不能被归类为成功。
- localhost、私网和 metadata 目标侧不应收到真实请求。
- 如果初始 URL 本身不在白名单，应在创建阶段 400 拒绝；该用例与 redirect 二次校验分开判断。

## 9. 敏感信息脱敏测试矩阵

目标：页面、列表 API、详情 API、attempts API 和错误信息都不能泄露 token、authorization、secret、password、key 等敏感信息；但实际投递到供应商的原始 Header 和 Body 不能因为展示脱敏而被修改。

### TC-M01 Header 展示脱敏

提交 payload：

```json
{
  "eventType": "security.mask.header",
  "sourceSystem": "qa-service",
  "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
  "headers": {
    "Authorization": "Bearer real-secret-token",
    "X-Vendor-Token": "vendor-token-123",
    "X-Trace-Id": "trace-visible-001"
  },
  "body": {"case": "header-mask"},
  "maxAttempts": 1
}
```

预期结果：

- 页面详情中的 Header 将 `Authorization`、`X-Vendor-Token` 显示为 `******` 或等价掩码。
- `X-Trace-Id` 这类非敏感 Header 可以明文展示。
- `/api/notifications` 列表预览不出现敏感 Header 明文。
- `/api/notifications/{id}` 详情响应不应向前端暴露敏感 Header 明文。

### TC-M02 JSON Body 展示脱敏

提交 Body：

```json
{
  "contactId": "C-10086",
  "accessToken": "body-token-123",
  "password": "plain-password",
  "profile": {
    "apiKey": "nested-key-123",
    "email": "user@example.com"
  }
}
```

预期结果：

- 页面详情和任何 Body 预览不显示 `body-token-123`、`plain-password`、`nested-key-123`。
- 非敏感字段如 `contactId`、`email` 可以展示。
- 嵌套 JSON 中的敏感字段也必须被递归脱敏。

### TC-M03 lastError 和 attempt error 脱敏

操作步骤：

1. 构造一个失败任务，Header 和 Body 中包含 token、password、apiKey。
2. 让目标返回 500 或触发网络错误。
3. 查看任务卡片 `lastError`、详情 `lastError` 和 attempts 中的 `error`。

预期结果：

- `lastError` 不包含敏感 Header 值。
- attempt `error` 不包含敏感 Header 值或敏感 Body 值。
- 错误信息可以保留必要诊断信息，例如 HTTP 状态码、网络错误类型和目标 host 的安全展示值。

### TC-M04 真实投递原文不受展示脱敏影响

操作步骤：

1. 使用 mock vendor 或临时测试 receiver 接收请求。
2. 提交包含敏感 Header 和敏感 Body 的通知。
3. 在 receiver 侧确认收到的请求原文。
4. 再回到页面详情和 attempts 检查展示内容。

预期结果：

- receiver 收到的 Header 和 Body 保持提交原文，供应商认证不会因脱敏逻辑失效。
- 通知服务页面、列表、详情、attempts 和错误信息仍只展示脱敏后的内容。
- 脱敏只影响“存储后展示 / API 返回给前端 / 错误记录”这一侧，不影响真实 HTTP 投递数据。

## 9A. 调用方 API Key 鉴权测试矩阵

目标：验证 `NOTIFICATION_API_KEYS` 默认关闭；开启后所有写接口都要求调用方 Key；读接口、健康检查、静态资源和 mock vendor 仍可用于本地调试；401 响应不泄露 Key，也不导致页面白屏。

### TC-K01 默认关闭时写接口不要求 Key

操作步骤：

1. 不设置 `NOTIFICATION_API_KEYS` 启动服务。
2. 不带任何鉴权 Header 调用 `POST /api/notifications`，目标使用 same-origin mock vendor。
3. 构造一个 `failed` 任务后，不带 Key 调用 `/api/notifications/{id}/retry` 或 `/api/notifications/retry`。

预期结果：

- 创建通知仍按原有契约返回 `201` 或幂等 `200`。
- 重试接口在状态允许时返回 `200`，不因为缺少 Key 返回 `401`。
- 该用例只验证默认关闭行为，不应把它作为生产安全配置。

### TC-K02 开启后缺失或错误 Key 返回 401

启动服务：

```bash
NOTIFICATION_API_KEYS=dev-caller-key python3 server.py --host 127.0.0.1 --port 8001
```

缺 Key 请求：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "auth-missing-key-001",
    "eventType": "auth.test",
    "sourceSystem": "test-plan",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "body": {"case": "missing-key"},
    "maxAttempts": 1
  }'
```

错误 Key 请求：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -H 'X-Notification-Api-Key: wrong-key' \
  -d '{
    "requestId": "auth-wrong-key-001",
    "eventType": "auth.test",
    "sourceSystem": "test-plan",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "body": {"case": "wrong-key"},
    "maxAttempts": 1
  }'
```

预期结果：

- 两个请求都返回 HTTP `401`。
- 响应体为 `{"error":"unauthorized"}` 或等价 ErrorResponse，不包含提交的 Key。
- `/api/notifications` 中不出现 `auth-missing-key-001` 或 `auth-wrong-key-001`。
- 服务端日志、浏览器控制台、页面错误提示、截图和工单说明都不应包含真实 Key。

### TC-K03 两种 Header 形式都可通过

操作步骤：

1. 使用 `NOTIFICATION_API_KEYS=dev-caller-key` 启动服务。
2. 使用 `X-Notification-Api-Key: dev-caller-key` 创建一条通知。
3. 使用 `Authorization: Bearer dev-caller-key` 创建另一条通知。
4. 使用 `Authorization: dev-caller-key`、`Authorization: Basic ...` 或空 Bearer 值各发一次写请求。

预期结果：

- 两种正确 Header 形式都返回 `201` 或幂等 `200`。
- 非 Bearer 或空 Bearer 的 `Authorization` 请求返回 `401`。
- 调用方 `Authorization` Header 不会进入通知任务的 `headers` 字段；只有 payload 中 `headers.Authorization` 才会被 Worker 投递给供应商。

### TC-K04 所有写接口受保护且 401 不改状态

测试准备：

- 开启 `NOTIFICATION_API_KEYS=dev-caller-key`。
- 准备一个 `failed` 任务和一个 `dead_letter` 任务，记录它们的 `status`、`deliveryRun`、`attemptCount`、`lastManualAction` 和 attempts 条数。

操作步骤：

1. 不带 Key 调用 `POST /api/notifications/{failedId}/retry`。
2. 不带 Key 调用 `POST /api/notifications/{failedId}/dead-letter`。
3. 不带 Key 调用 `POST /api/notifications/retry`。
4. 分别补上正确 Key 重复上述三个请求。
5. 查询相关任务详情和 attempts。

预期结果：

- 缺 Key 的三个写接口都返回 `401`。
- 缺 Key 的 retry 不会增加 `deliveryRun`，不会清空 `attemptCount`，不会新增 attempts。
- 缺 Key 的 dead-letter 不会修改 `status`、`lastManualAction`、`lastManualActionBy` 或 `resolutionNote`。
- 缺 Key 的批量 retry 不会把任何任务重新入队，响应也不应伪造 `count/items` 成功结果。
- 正确 Key 请求按各自接口契约执行。

### TC-K05 读接口和调试入口不受调用方 Key 保护

操作步骤：

1. 开启 `NOTIFICATION_API_KEYS=dev-caller-key`。
2. 不带 Key 分别访问 `/`、`/app.js`、`/styles.css`、`/health`、`/api/stats`、`/api/notifications`、`/api/notifications/export.csv`。
3. 对一个已存在任务不带 Key 访问 `/api/notifications/{id}` 和 `/api/notifications/{id}/attempts`。
4. 使用 same-origin `/mock/vendor/crm` 完成本地供应商调试。

预期结果：

- 静态资源和页面可加载，便于白屏调试。
- `/health`、`/api/stats`、列表、导出、详情和 attempts 不因缺少调用方 Key 返回 `401`。
- 返回内容仍应遵守脱敏规则，不展示敏感 Header、Body、lastError 或 attempt error 原文。
- 生产化如果需要保护读接口，应在后续权限模型中单独补充，不能误认为第一版共享 Key 已覆盖所有数据读取。

### TC-K06 开启鉴权时的白屏调试

目的：确认页面或手工调试在写接口返回 `401` 时能清楚暴露鉴权问题，而不是整页空白或误判为服务不可用。

操作步骤：

1. 使用 `NOTIFICATION_API_KEYS=dev-caller-key` 启动服务并打开首页。
2. 确认 `/`、`/app.js`、`/styles.css`、`/health` 正常返回。
3. 如果页面没有输入调用方 Key 的控件，直接点击 `提交通知`。
4. 在 Network 面板查看 `POST /api/notifications` 响应。
5. 使用正确 Key 的 curl 创建通知，确认后端写接口本身可用。

预期结果：

- 页面加载、状态小条、列表和详情不应因为开启 `NOTIFICATION_API_KEYS` 白屏。
- 未带 Key 的页面提交应返回 `401`，页面应显示错误提示或至少保留可继续操作的表单，不应出现未处理 JS 异常。
- Network 面板能清楚看到 `401` 和 `{"error":"unauthorized"}`。
- 白屏排查截图、控制台日志和工单记录中只写“Key 缺失/错误”，不要包含真实 Key。
- 如果页面暂未支持配置调用方 Key，应在测试记录中说明“鉴权写操作通过 curl 验证，页面写操作因缺 Key 返回 401”，不要误记为后端故障。

## 10. 失败分类与错误类型测试矩阵

目标：确认“任务级失败分类”和“单次尝试错误类型”一致记录，便于页面筛查和白屏时 API 定位问题。

字段口径：

| 层级 | 字段 | 成功预期 | 失败预期 |
| --- | --- | --- | --- |
| `/api/notifications/{id}` | `failureType` | `null` 或空 | `http_error`、`timeout`、`network_error`、`delivery_error` 之一 |
| `/api/notifications/{id}/attempts` | `errorType` | `null` 或空 | 与本次失败原因一致 |

### TC-E01 http_error 分类

提交目标地址：

```text
http://127.0.0.1:8001/mock/vendor/crm?status=429
```

操作步骤：

1. 提交通知，`maxAttempts` 设置为 `1`。
2. 等待任务进入 `最终失败`。
3. 查看 `/api/notifications/{id}` 和 `/api/notifications/{id}/attempts`。

预期结果：

- 任务 `status` 为 `failed`。
- 任务 `lastStatusCode` 为 `429`。
- 任务 `lastError` 显示 `target returned HTTP 429`。
- 任务 `failureType` 为 `http_error`。
- attempts 最后一条 `statusCode` 为 `429`，`errorType` 为 `http_error`。
- 页面详情“失败类型”展示 `http_error`，attempts 中本次错误类型不为空。

### TC-E02 timeout 分类

启动服务时设置较短投递超时：

```bash
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=0.2 python3 server.py --host 127.0.0.1 --port 8001
```

提交目标地址：

```text
http://127.0.0.1:8001/mock/vendor/crm?delayMs=1000
```

预期结果：

- 任务最终失败或等待重试时，`lastStatusCode` 为 `null`。
- `lastError` 以 `timeout:` 开头或明确包含 timeout 语义。
- `/api/notifications/{id}` 中 `failureType` 为 `timeout`。
- `/api/notifications/{id}/attempts` 最后一条 `errorType` 为 `timeout`。
- `durationMs` 应接近配置超时，不应等待完整 `delayMs` 才返回。

### TC-E03 network_error 分类

使用一个允许访问但无法连接的外部测试目标，例如测试环境提供的公开 receiver 关闭端口；不要使用 localhost、私网或 link-local，因为这些应在创建阶段被 SSRF 校验拒绝。

预期结果：

- 任务投递失败，`lastStatusCode` 为 `null`。
- `lastError` 以 `network error:` 开头或明确包含连接失败原因。
- `/api/notifications/{id}` 中 `failureType` 为 `network_error`。
- `/api/notifications/{id}/attempts` 最后一条 `errorType` 为 `network_error`。
- 该用例必须是 Worker 投递阶段失败，不是创建任务阶段 400。

### TC-E04 delivery_error 分类

该分类用于兜底不可预期异常，建议通过单元测试或临时测试替身触发，例如 monkeypatch `urlopen` 抛出非 `HTTPError`、非 `URLError`、非 `TimeoutError` 的异常。

预期结果：

- 任务失败或等待重试。
- `lastError` 以 `delivery error:` 开头。
- `/api/notifications/{id}` 中 `failureType` 为 `delivery_error`。
- `/api/notifications/{id}/attempts` 最后一条 `errorType` 为 `delivery_error`。
- 前端详情能展示该类型，不应因为未知兜底错误白屏。

## 11. Mock Vendor 参数兼容测试

目标：确认本地 mock vendor 能稳定模拟常见供应商异常，方便人工和 smoke 用例复用。

| 用例 | targetUrl | 预期 |
| --- | --- | --- |
| TC-V01 | `/mock/vendor/crm?status=429` | mock 返回 HTTP 429；任务记录 `lastStatusCode=429`、`failureType=http_error`、attempt `errorType=http_error` |
| TC-V02 | `/mock/vendor/crm?status=503` | mock 返回 HTTP 503；任务按重试策略处理，最终失败时 `lastStatusCode=503` |
| TC-V03 | `/mock/vendor/crm?delayMs=1000` | mock 至少延迟约 1000ms；如果超出投递超时，应归类为 `timeout` |
| TC-V04 | `/mock/vendor/crm?fail=1` | 保持旧兼容行为，默认返回 HTTP 500 |
| TC-V05 | `/mock/vendor/crm?fail=1&status=503` | `fail=1` 仍进入失败响应分支，状态码可由 `status=503` 覆盖 |
| TC-V06 | `/mock/vendor/crm?status=200&delayMs=200` | 延迟后成功返回，任务可进入 `succeeded`，`failureType` 为空 |

统一验收点：

- `status` 只接受 3 位数字；非法 status 不应导致 mock vendor 崩溃。
- `delayMs` 非法值按 0 处理；负数按 0 处理。
- `fail=1`、`fail=true`、`fail=yes` 都应兼容。
- mock 响应 JSON 中保留 `vendor`、`received`、`status`、`method`、`body` 等诊断字段时，页面不依赖这些字段渲染。

## 12. 投递 Timeout 配置测试

配置项：全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` 和创建通知请求中的单任务 `timeoutSeconds`。

当前口径：

| 层级 | 场景 | 配置值 | 预期实际超时 |
| --- | --- | --- | --- |
| 全局环境变量 | 默认值 | 未设置 | `8.0` 秒 |
| 全局环境变量 | 小数值 | `0.2` | `0.2` 秒 |
| 全局环境变量 | 最小边界 | `0`、负数、`0.01` | clamp 到 `0.1` 秒 |
| 全局环境变量 | 最大边界 | `120` | clamp 到 `60.0` 秒 |
| 全局环境变量 | 非法值 | `abc`、空白非数字 | 回退到默认 `8.0` 秒 |
| 单任务字段 | 省略或 `null` | 无覆盖值 | 使用 Worker 投递时当前进程的全局配置 |
| 单任务字段 | 有限数字 | `2`、`0.2` | clamp 到 `0.1` 到 `60.0` 秒并持久化到任务 |
| 单任务字段 | 最小边界 | `0`、负数、`0.01` | clamp 到 `0.1` 秒 |
| 单任务字段 | 最大边界 | `120` | clamp 到 `60.0` 秒 |
| 单任务字段 | 非法类型 | `true`、`"abc"`、对象、数组、非有限数字 | 创建接口返回 HTTP `400`，不创建任务 |

### TC-T01 默认 timeout

操作步骤：

1. 不设置 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` 启动服务。
2. 提交 `targetUrl=http://127.0.0.1:8001/mock/vendor/crm?delayMs=9000`，`maxAttempts=1`。

预期结果：

- 任务应在约 8 秒附近失败为 `timeout`，不应等待完整 9 秒以上太久。
- `failureType=timeout`，attempt `errorType=timeout`。

### TC-T02 小数 timeout

操作步骤：

1. 使用 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=0.2` 启动服务。
2. 提交 `delayMs=1000` 的 mock vendor 任务。

预期结果：

- 任务在约 200ms 后记录 timeout。
- `durationMs` 为非负整数，并明显小于 `delayMs`。

### TC-T03 非法值和边界值

操作步骤：

1. 分别用 `abc`、`0`、`0.01`、`120` 启动服务。
2. 配合 `delayMs` 构造超时任务。

预期结果：

- 非法值不应导致服务启动失败。
- `0`、负数和过小值按 `0.1` 秒处理。
- 过大值按 `60.0` 秒处理。
- 所有超时失败仍写入 `failureType=timeout` 和 attempt `errorType=timeout`。

### TC-T04 单任务 timeoutSeconds 覆盖全局默认

操作步骤：

1. 使用较大的全局默认启动服务：

```bash
NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=8 python3 server.py --host 127.0.0.1 --port 8001
```

2. 提交一个任务级 `timeoutSeconds=0.2`、mock vendor 延迟 1000ms、`maxAttempts=1` 的任务：

```bash
curl -sS -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "timeout-override-001",
    "eventType": "timeout.override",
    "sourceSystem": "test-plan",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm?delayMs=1000",
    "body": {"case": "task-timeout"},
    "maxAttempts": 1,
    "timeoutSeconds": 0.2
  }'
```

3. 记录返回的任务 `id`，等待任务完成后查询详情和 attempts：

```bash
curl http://127.0.0.1:8001/api/notifications/{id}
curl http://127.0.0.1:8001/api/notifications/{id}/attempts
```

预期结果：

- 创建接口返回 HTTP `201`，任务成功创建。
- `/api/notifications/{id}` 返回 `timeoutSeconds=0.2`。
- 任务不等待全局 8 秒，而是在约 200ms 后失败或进入最终失败流程。
- `failureType=timeout`，最后一条 attempt `errorType=timeout`。
- 该任务的超时行为由任务级字段决定，不由全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=8` 决定。

### TC-T05 单任务 timeoutSeconds 省略、null、边界和非法值

操作步骤：

1. 使用 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS=0.5` 启动服务。
2. 提交不带 `timeoutSeconds` 的任务，目标 `delayMs=1000`，`maxAttempts=1`。
3. 提交 `"timeoutSeconds": null` 的任务，目标 `delayMs=1000`，`maxAttempts=1`。
4. 提交 `"timeoutSeconds": 0.01` 的任务，目标 `delayMs=1000`，`maxAttempts=1`。
5. 提交 `"timeoutSeconds": 120` 的任务，目标 `delayMs=1000`，`maxAttempts=1`。
6. 提交非法值请求：

```bash
curl -i -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/crm",
    "timeoutSeconds": true
  }'
```

7. 再提交 `"timeoutSeconds": "abc"`、`"timeoutSeconds": {"seconds": 1}` 和 `"timeoutSeconds": [1]` 的非法请求。

预期结果：

- 省略 `timeoutSeconds` 的任务详情中该字段为 `null`，投递时使用全局 `0.5` 秒。
- `timeoutSeconds=null` 与省略字段行为一致。
- `timeoutSeconds=0.01` 的任务详情中应显示 clamp 后的 `0.1`，并按约 100ms 超时。
- `timeoutSeconds=120` 的任务详情中应显示 clamp 后的 `60.0`；在 `delayMs=1000` 场景下不应因为任务级超时失败。
- 非法值请求返回 HTTP `400`，响应体包含 `error`，且不会在 `/api/notifications` 中创建新任务。
- 非法任务级字段不影响后续合法任务创建，也不改变全局 `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS`。

## 13. 前端 Request ID / 幂等与调试控件测试

### TC-P01 填写 requestId 并展示详情

操作步骤：

1. 在表单 `Request ID` 填入 `manual-request-001`。
2. 点击 `成功示例` 后确认该字段未被意外清空；如果示例会重置字段，需要重新填写。
3. 点击 `提交通知`。
4. 打开任务详情。

预期结果：

- 提交请求体包含 `requestId`。
- `/api/notifications/{id}` 返回 `requestId=manual-request-001`。
- 详情弹窗 `Request ID` 展示 `manual-request-001`。
- 页面列表刷新不应丢失任务或白屏。

### TC-P02 重复提交同 requestId

操作步骤：

1. 使用同一个 `requestId=manual-request-duplicate-001` 提交一次成功任务。
2. 记录返回的任务 `id`。
3. 不修改 `requestId`，再次点击 `提交通知`。
4. 查看提示、列表和详情。

预期结果：

- 第二次提交后端返回 HTTP 200，`duplicate=true`。
- 第二次返回的 `id` 与第一次一致。
- `/api/notifications` 中不会新增第二条同 requestId 任务。
- 页面提示应能让测试者判断这是幂等命中，而不是新任务入队。

### TC-P05 API 级 requestId 幂等回归

目的：绕过前端直接验证后端 `requestId` 幂等语义，防止页面提示正常但 API 实际创建重复任务。

第一次提交：

```bash
curl -sS -X POST http://127.0.0.1:8001/api/notifications \
  -H 'Content-Type: application/json' \
  -d '{
    "requestId": "api-idempotency-001",
    "eventType": "user.registered",
    "sourceSystem": "user-service",
    "targetUrl": "http://127.0.0.1:8001/mock/vendor/ad-system",
    "method": "POST",
    "headers": {"X-Vendor-Token": "demo-token"},
    "body": {"userId": "U-IDEMP-001"},
    "maxAttempts": 3
  }'
```

第二次使用完全相同的 `requestId` 再提交一次。Body 中其他字段可以保持一致，也可以刻意改动 `body.userId` 或 `targetUrl`，用于确认服务是否坚持返回已有任务。

查询校验：

```bash
curl "http://127.0.0.1:8001/api/notifications?eventType=user.registered&sourceSystem=user-service"
```

预期结果：

- 第一次提交返回 HTTP 201，`duplicate=false`。
- 第二次提交返回 HTTP 200，`duplicate=true`。
- 两次响应中的 `id` 完全相同。
- 通知列表中同一个 `requestId=api-idempotency-001` 只存在一条任务。
- 第二次提交不应覆盖第一次任务的 `targetUrl`、Header、Body、状态、attempts 或 `createdAt`。
- 如果第一次任务已经 `succeeded`，第二次重复提交不能把任务重新置为 `queued`。
- 如果第二次提交不带 `requestId`，应创建新任务；幂等只对非空 `requestId` 生效。

### TC-P03 暂停自动刷新

操作步骤：

1. 打开首页并确认列表会自动刷新。
2. 点击 `暂停自动刷新`。
3. 提交或等待任务状态变化。
4. 观察列表不会被定时刷新覆盖。
5. 再次点击恢复自动刷新，或手动点击 `刷新`。

预期结果：

- 按钮 `aria-pressed` 与当前自动刷新状态一致。
- 暂停后不会继续触发 2.5 秒一次的列表请求。
- 恢复后列表可以继续自动刷新。
- 若控件绑定失败，控制台应能看到具体 JS 异常，页面不应整体白屏。

### TC-P04 复制 curl

操作步骤：

1. 填写表单，包括 `requestId`、Header JSON、Body JSON。
2. 点击 `复制 curl`。
3. 将剪贴板内容粘贴到终端检查。

预期结果：

- curl 使用当前页面 origin 的 `/api/notifications`。
- curl payload 包含 `requestId`、`eventType`、`sourceSystem`、`targetUrl`、`method`、`maxAttempts`、`headers`、`body`。
- Header/Body JSON 非法时，应提示 JSON 格式错误，不应复制损坏命令。
- 复制动作失败时有页面提示，不应抛出未处理异常。

### TC-P06 详情复制 requestId / targetUrl

目的：验证详情抽屉提供的复制能力可以支持白屏和供应商投递排障，且复制失败不会影响页面渲染。

操作步骤：

1. 创建一条带 `requestId=detail-copy-001` 的任务，目标地址使用 `http://127.0.0.1:8001/mock/vendor/crm`。
2. 打开该任务详情抽屉。
3. 点击 `复制 requestId` 或等价按钮。
4. 将剪贴板内容粘贴到终端或文本框检查。
5. 点击 `复制 targetUrl` 或等价按钮。
6. 将复制出的目标地址与 `/api/notifications/{id}` 返回的 `targetUrl`、Network 面板中的详情响应和供应商日志目标进行对照。
7. 在浏览器禁用剪贴板权限或模拟 `navigator.clipboard` 不可用，再点击复制按钮。

预期结果：

- 详情抽屉展示的 `requestId` 与 API 详情中的 `requestId` 一致，复制内容不包含额外空白或标签文案。
- 详情抽屉展示的 `targetUrl` 与 API 详情中的 `targetUrl` 一致；如果页面展示做了换行或截断，复制内容仍应是完整 URL。
- 复制成功时有轻量提示，不应关闭抽屉或刷新列表。
- 复制失败时有错误提示或降级方案，不应抛出未处理异常，也不应导致整页白屏。
- 当 `requestId` 为空时，复制按钮应禁用或提示无可复制内容；不能复制字符串 `null`、`undefined`。
- 该用例只验证调试复制能力，不改变投递、重试、白名单或脱敏语义。

## 14. 前端白屏调试与验收步骤

白屏调试建议使用 `8001` 端口；如果实际启动端口不同，将下面 URL 中端口替换为实际端口。

### 快速入口

1. 启动服务：

```bash
python3 server.py --host 127.0.0.1 --port 8001
```

2. 打开页面：

```text
http://127.0.0.1:8001/
```

3. 分别打开静态资源：

```text
http://127.0.0.1:8001/app.js
http://127.0.0.1:8001/styles.css
```

4. 打开健康检查：

```text
http://127.0.0.1:8001/health
```

验收标准：

- 首页不是空白页，左侧表单和右侧任务列表都可见。
- `app.js` 返回 JavaScript 内容，不是 404 或 HTML 错误页。
- `styles.css` 返回 CSS 内容，不是 404 或 HTML 错误页。
- `/health` 返回结构化 JSON，`status` 为 `ok` 或可解释的 `degraded`，且包含 `database`、`worker`、`queue.counts`、`queue.readyCount`、`now`。
- 如果启动时设置了 `NOTIFICATION_API_KEYS`，首页、静态资源、`/health`、列表和详情仍应可用于白屏排查；只有创建通知、重试、死信和批量重试这些写接口会因为缺 Key 返回 `401`。
- 截图、控制台日志和工单只记录“缺少或错误调用方 Key”，不要粘贴真实 `X-Notification-Api-Key` 或 Bearer 值。

### Health / Worker 状态小条白屏调试

状态小条用于快速判断页面、数据库、Worker 和队列是否一起健康。若首页白屏或状态条不显示，优先检查这些入口：

| 检查项 | 链接或按钮 | 健康判断字段 |
| --- | --- | --- |
| 首页 | `http://127.0.0.1:8001/` | 能看到 Health/Worker 状态小条、表单和任务列表 |
| 健康接口 | `http://127.0.0.1:8001/health` | `status`、`database.ok`、`database.path`、`worker.alive`、`queue.counts`、`queue.readyCount`、`now` |
| 静态 JS | `http://127.0.0.1:8001/app.js` | 返回 JS 内容，状态条相关 DOM 绑定无语法错误 |
| 静态 CSS | `http://127.0.0.1:8001/styles.css` | 返回 CSS 内容，状态条不因样式缺失不可见 |
| 列表 API | `http://127.0.0.1:8001/api/notifications` | 返回 `{ "items": [...], "pagination": {...} }`，用于和 `queue.counts` 交叉核对 |

页面上需要重点点击或观察的按钮：

- `刷新`：触发任务列表刷新后，状态小条也应保持可见，不应被重新渲染清空。
- `暂停自动刷新`：暂停列表轮询时不应停止 Health/Worker 状态展示的初始渲染；恢复后状态仍能更新。
- `成功示例` / `失败示例` / `提交通知`：提交任务后，`queue.counts` 和 `queue.readyCount` 应随任务流转变化。
- `详情`：打开弹窗时状态小条不应消失；详情请求失败也不能导致整页白屏。

状态字段判断：

- 健康：`status=ok`、`database.ok=true`、`worker.alive=true`，且 `now` 可解析。
- 队列健康：`queue.counts` 中状态计数为数字，`queue.readyCount` 为数字且不会是负数。
- Worker 可观测：最近 tick、最近 claimed job、最近错误字段缺失时应有页面兜底显示；字段存在但为 `null` 时不能触发 JS 异常。
- 数据库异常：`status=degraded` 或 `database.ok=false` 时，状态小条应显示降级/异常文案，但首页其余功能区不应整体白屏。

### TC-W01 白屏健康条判断

目的：验证首页状态小条能作为白屏调试第一入口，帮助判断问题出在静态资源、健康接口、数据库、Worker 还是队列。

操作步骤：

1. 启动服务并打开 `http://127.0.0.1:8001/`。
2. 确认页面顶部或列表区域附近能看到 Health/Worker 状态小条。
3. 点击状态小条中的 `刷新健康` 或等价按钮。
4. 同时打开 `http://127.0.0.1:8001/health`，对比页面展示和原始 JSON。
5. 点击 `成功示例`，再点击 `提交通知`，观察任务从 `queued` 到 `succeeded` 期间状态小条是否持续渲染。
6. 点击 `失败示例`，将 `maxAttempts` 改为 `1` 后提交，观察出现 `failed` 后状态小条和列表是否都能刷新。
7. 点击 `暂停自动刷新`，确认列表轮询暂停时，手动点击 `刷新健康` 仍能更新状态小条。
8. 打开失败任务 `详情`，确认弹窗打开、attempts 加载或失败时，状态小条不消失、不白屏。

预期结果：

- 首页可见状态小条、提交表单和任务列表；如果状态小条异常，页面其他功能区也不能整体白屏。
- `刷新健康` 按钮会触发 `/health` 请求，返回成功时页面展示 `status`、数据库状态、Worker 状态和队列数量。
- `/health` 原始 JSON 中 `database.ok=true` 时，页面不应显示数据库异常。
- `/health` 原始 JSON 中 `worker.alive=true` 时，页面不应显示 Worker 停止；若 `worker.lastError` 不为空，应展示或兜底显示最近错误。
- `queue.counts` 和 `queue.readyCount` 在页面上应显示为数字或清晰的空值，不应出现 `undefined`、`NaN` 或 JS 报错。
- 提交成功任务、失败任务、打开详情、暂停自动刷新、手动刷新健康，都不应导致状态小条 DOM 被清空。
- 浏览器控制台不应出现由 health 字段缺失、`null` 值或类型变化引起的 `Cannot read properties of null/undefined`。

### TC-W02 列表分页、排序和导出白屏调试

目的：验证新增分页、排序和 CSV 导出控件不会让首页白屏；接口异常时页面应给出提示并保留当前列表。

操作步骤：

1. 打开 `http://127.0.0.1:8001/`，确认表单、任务列表和 Health/Worker 状态小条可见。
2. 在列表过滤区域设置 `status`、`eventType`、`sourceSystem`、`targetUrl`、`createdFrom`、`createdTo`、`updatedFrom`、`updatedTo` 和 `limit`，点击查询。
3. 如果页面提供分页控件，点击 `下一页`、`上一页` 或等价按钮；如果提供页码输入，输入 `offset=0`、`offset=2` 分别查询。
4. 如果页面提供排序控件，依次选择 `createdAt desc`、`createdAt asc` 和 `status asc`。
5. 点击 `导出 CSV` 或等价按钮，观察浏览器下载行为和 Network 请求。
6. 在 Network 面板确认列表请求带上 `limit`、`offset`、`sort`、`order` 和合法时间范围，导出请求带上当前过滤、时间范围、分页和排序 query。
7. 临时把 query 改成非法参数，例如 `sort=headers`、`offset=abc` 或 `createdFrom=not-a-time`，刷新或手动请求，观察页面错误提示。
8. 检查浏览器控制台和页面 DOM。

预期结果：

- 分页、排序、导出控件存在或缺失时都不应导致首页白屏；如果某项功能尚未接入页面，应至少能通过 API 调试。
- 分页或排序请求成功后，任务列表继续显示任务卡片或空状态，不能出现 `undefined`、`NaN` 或布局错乱。
- 切换过滤条件后，分页应回到第一页或明确重置 `offset`，避免用户看到旧 offset 导致的空结果误判。
- 导出成功时触发 CSV 下载或展示可下载链接，页面不应被 CSV 文本替换成纯文本白屏。
- 导出失败或参数非法时，页面展示错误提示，列表和状态小条仍保持可用。
- CSV 下载内容中不能出现敏感 Header 原文或完整 Body 原文。
- 控制台不应出现由 `pagination` 缺失、导出响应非 JSON、下载链接释放、按钮 loading 状态等导致的阻断渲染异常。

### 页面交互验收

操作步骤：

1. 点击 `成功示例`，确认目标地址自动填为当前页面 origin 下的 `/mock/vendor/crm`。
2. 点击 `提交通知`，确认出现 `已入队：{id}` 提示。
3. 点击 `刷新`，等待右侧任务卡片显示 `投递成功`。
4. 点击该卡片的 `详情`。
5. 在详情弹窗查看状态、Header、Body、lastError 和 attempts。
6. 关闭详情弹窗。
7. 点击 `失败示例`，将 `最大尝试次数` 改为 `2`。
8. 点击 `提交通知`，等待状态从 `等待重试` 变为 `最终失败`。
9. 点击失败任务的 `详情`，检查 attempts 至少 2 条。
10. 点击 `重新投递`，确认任务重新入队，且详情 attempts 追加新记录。
11. 填写 `Request ID` 后提交，打开详情确认 requestId 展示。
12. 在详情抽屉复制 `requestId` 和 `targetUrl`，与 API 详情响应对照。
13. 点击 `暂停自动刷新`，确认列表不再定时刷新；再次点击后恢复。
14. 点击 `复制 curl`，确认剪贴板命令包含当前表单 payload。
15. 对 `status=429` 或 `delayMs` 超时任务打开详情，确认 `failureType` 和 attempts `errorType` 展示。
16. 在列表过滤表单中依次选择状态、填写 `eventType`、`sourceSystem`、`targetUrl`、`createdFrom`、`createdTo`、`updatedFrom`、`updatedTo` 和 `limit`，点击查询或刷新。
17. 点击 `清空过滤`，确认列表恢复为无过滤条件。
18. 构造多个 `failed` 任务后点击 `批量重试`，确认按钮状态、提示文案和列表刷新。
19. 打开批量重试后的任务详情，检查 attempts 中 `deliveryRun` 和 `attemptSequence` 展示。
20. 如果页面提供分页和排序控件，切换页码、排序字段和排序方向，确认列表请求与页面展示一致。
21. 如果页面提供 `导出 CSV`，在过滤后点击导出，确认下载内容与当前条件一致且不包含敏感 Header 或完整 Body 原文。

成功判断：

- 成功任务卡片显示 `投递成功`，尝试次数通常为 `1/5` 或 `1/1`。
- 成功任务 attempts 中最后一条为 `succeeded`，状态码为 `200`。
- 失败任务卡片最终显示 `最终失败`。
- 失败任务 attempts 中至少 2 条 `failed`，状态码为 `500`。
- 失败详情能展示 `failureType`，attempts 能展示或通过 API 返回 `errorType`。
- 详情 attempts 能展示 `deliveryRun` 和全历史递增的 `attemptSequence`。
- requestId、详情复制 requestId/targetUrl、暂停自动刷新、复制 curl 控件可操作，且不会产生阻断渲染的异常。
- 过滤表单提交后只显示匹配任务，`清空过滤` 后恢复全部列表。
- `批量重试` 只影响 failed 任务，成功后列表中对应任务变为 `等待投递` 或后续投递状态。
- 分页、排序和导出 CSV 操作不会清空列表区域，不会让 Health/Worker 状态小条消失。
- 弹窗打开和关闭不会导致页面白屏，任务列表仍可刷新。
- 浏览器控制台无阻断渲染的 JavaScript 异常。

失败判断：

- 页面只有背景或空白，没有表单和任务列表。
- 点击 `成功示例`、`失败示例`、`刷新`、`详情` 无响应。
- 过滤表单输入后列表不刷新，或 `清空过滤` 后仍带旧 query 参数。
- 详情复制 requestId/targetUrl 按钮无响应、复制出 `undefined`，或复制失败导致抽屉和首页白屏。
- 点击 `批量重试` 后按钮永久 loading、重复提交、无提示，或把非 failed 任务改成 queued。
- 分页后列表和分页状态不一致，或排序控件显示一种顺序但 Network 请求发送了另一种顺序。
- 点击 `导出 CSV` 后当前页面被 CSV 文本覆盖、按钮永久 loading、下载失败没有提示，或 CSV 泄露 Header/Body 敏感原文。
- 详情弹窗一直显示 `正在加载...`，且控制台出现接口错误。
- attempts 请求返回 404、500 或 JSON 结构不是 `{ "items": [...] }`。
- `copyCurl`、`autoRefreshToggle`、`detailRequestId`、`detailFailureType`、`filterForm`、`clearFilters`、`bulkRetry`、`detailDeliveryRun`、`detailAttemptSequence` 等 DOM 节点存在但 JS 未绑定，控制台出现 `Cannot read properties of null` 或类似异常。

### 白屏快速定位顺序

1. 打开浏览器控制台，先看是否有 `app.js` 语法错误、空 DOM 引用或未处理 Promise 异常。
2. 分别访问 `/app.js`、`/styles.css`、`/api/notifications`，确认静态资源和列表 API 都返回正确内容。
3. 如果写请求返回 `401`，先确认是否设置了 `NOTIFICATION_API_KEYS`；这是鉴权失败，不等同静态资源白屏、后端 500 或 Worker 停止。记录时不要贴真实 Key。
4. 在控制台检查 `document.querySelector("#copyCurl")`、`document.querySelector("#autoRefreshToggle")`、`document.querySelector("#detailRequestId")`、`document.querySelector("#detailFailureType")` 是否存在。
5. 检查过滤和批量重试相关节点是否存在并完成绑定，例如 `filterForm`、`clearFilters`、`bulkRetry`、状态下拉、`eventType`、`sourceSystem`、`targetUrl`、时间范围和 `limit` 输入框。
6. 在 Network 面板观察列表请求 query string，确认 `status`、`eventType`、`sourceSystem`、`targetUrl`、`createdFrom`、`createdTo`、`updatedFrom`、`updatedTo`、`limit` 只在有值时发送，点击 `清空过滤` 后 query 被清掉。
7. 继续观察列表请求 query string，确认 `offset`、`sort`、`order` 只发送合法值；切换过滤条件后 `offset` 是否被重置。
8. 点击 `导出 CSV` 时观察 `GET /api/notifications/export.csv`，确认 query 复用了当前过滤、时间范围、分页和排序条件，响应不是 JSON 详情，也不会覆盖当前页面。
9. 点击 `批量重试` 时观察 `POST /api/notifications/retry`，如果启用了 `NOTIFICATION_API_KEYS` 应带正确 Key；缺 Key 的 `401` 不应让按钮卡在禁用或 loading 状态。
10. 打开一个失败任务详情，直接请求 `/api/notifications/{id}` 和 `/api/notifications/{id}/attempts`，确认 JSON 中有 `failureType` / `errorType` / `deliveryRun` / `attemptSequence`。
11. 检查详情 attempts 区域的 DOM，确认 `deliveryRun` 与 `attemptSequence` 字段缺失时有安全兜底，不会因为 undefined 字段白屏。
12. 暂停自动刷新后再打开详情，排除列表定时刷新覆盖调试现场。
13. 使用详情抽屉复制 `requestId` 和 `targetUrl`，与 `/api/notifications/{id}` 原始 JSON 对比，确认白屏不是由详情字段缺失或复制控件空引用触发。
14. 使用 `复制 curl` 生成命令，与 Network 面板中的实际 POST payload 对比，确认 requestId 和 JSON 字段没有丢失；如果启用了鉴权，复制结果不应自动包含真实调用方 Key。
15. 检查 Health/Worker 状态小条读取 `/health` 后是否安全渲染 `status`、`serviceVersion`、`schemaVersion`、`database.ok/path`、`worker.alive`、`queue.counts`、`queue.readyCount` 和 `now`。
16. 若状态小条白屏，先用 `/health` 原始 JSON 判断是接口缺字段、字段类型变化、版本错配，还是前端对 `worker.lastTickAt`、`worker.lastClaimedJobId`、`worker.lastError` 等可空字段没有兜底。

## 15. 8 小时 / 50+ 轮迭代回归检查清单

长周期、多 agent 迭代后，至少执行以下检查，防止局部修复引入跨层回归。

### 后端 API

- `/health` 返回 200，且 schema 包含 `status`、`serviceVersion`、`schemaVersion`、`database.ok`、`database.path`、`worker.alive`、`queue.counts`、`queue.readyCount`、`now`。
- `/health` 如实现 Worker 并发字段，应包含或兼容表达 `worker.concurrency`、`worker.threadCount`、`worker.aliveCount`；字段缺失时前端按单 Worker 旧版本兜底。
- `/health` 在数据库正常时 `status=ok`、`database.ok=true`；数据库异常时能返回 `degraded` 或明确异常状态，不返回误导性 `ok`。
- `/health` 的 `queue.counts` 与 `/api/notifications` 中各状态任务数量语义一致，`queue.readyCount` 能反映当前可领取任务。
- `/api/notifications` 支持全部列表、状态筛选、`eventType`、`sourceSystem`、`targetUrl` 和 `limit` 组合过滤。
- `/api/notifications` 支持 `createdFrom`、`createdTo`、`updatedFrom`、`updatedTo` 时间范围过滤；非法时间返回 400，不返回 500。
- `/api/notifications` 支持 `offset`、`sort`、`order`，并返回 `pagination`；分页和排序不能破坏过滤条件和时间范围。
- `/api/notifications/export.csv` 支持与列表一致的过滤、时间范围、分页和排序，返回 CSV，不导出 Header 原文或完整 Body 原文。
- `/api/notifications/export.csv` 对非法时间范围返回 400，不能下载全部历史任务或包含敏感数据的 CSV。
- `/api/notifications/{id}` 对存在任务返回详情，对不存在任务返回 404。
- `/api/notifications/{id}/attempts` 对成功任务返回 1 条成功尝试，对失败任务返回多条失败尝试，对不存在任务返回 404。
- `/api/notifications/{id}/attempts` 中 `attemptNumber` 是本轮次数，`attemptSequence` 是全历史递增序号，`deliveryRun` 是投递轮次。
- `/api/notifications/{id}/retry` 对失败任务可重新入队，对不存在任务返回 404，对投递中任务返回 409。
- `/api/notifications/retry` 可批量重试 failed 任务，返回 `count/items`，被重试任务进入 `queued`，非 failed 状态不被修改。
- 设置 `NOTIFICATION_API_KEYS` 后，`POST /api/notifications`、单任务 retry、dead-letter 和批量 retry 缺 Key 或错 Key 返回 `401`，且响应为 `{"error":"unauthorized"}` 或等价 ErrorResponse。
- 写接口 401 不应创建任务、重新入队、标记死信、增加 `deliveryRun` 或新增 attempts。
- `requestId` 幂等语义不被破坏：重复提交同一 requestId 返回已有任务而不是创建新任务。
- `/api/notifications/{id}` 返回任务级 `failureType`；成功任务为空，失败任务按原因填充。
- `/api/notifications/{id}/attempts` 返回尝试级 `errorType`；成功尝试为空，失败尝试按原因填充。
- `/health` 至少能支持运维判断数据库、Worker、队列积压、lease 回收和前后端版本错配；如果实现 `/api/stats`，统计口径和版本字段应与 `/health` 和列表 API 一致。

### 投递语义

- Worker 状态可观测：启动时间、最近 tick、最近 claimed job、最近错误字段能通过 `/health` 或等价接口查看。
- Worker 正常运行时 `worker.alive=true`，最近 tick 不应长期停滞；发生投递失败后最近错误字段能帮助定位原因。
- `NOTIFICATION_WORKER_CONCURRENCY` 默认按 `1` 处理；建议验收 `1`、`2` 和非法值，确认 `/health` 字段、启动行为和日志符合约定。
- 多 Worker 并发领取时，同一任务同一投递轮次不应被重复领取或写出重复真实 HTTP attempt；失败、手动重试和 lease 回收后 attempts 历史仍应递增且可解释。
- 成功 mock vendor 仍能 1 次投递成功。
- 失败 mock vendor 仍按指数退避自动重试，达到 `maxAttempts` 后进入 `failed`。
- mock vendor `status=429`、`status=503`、`delayMs`、`fail=1` 都可用于构造测试。
- `http_error`、`timeout`、`network_error`、`delivery_error` 四类失败分类至少有自动或人工用例覆盖。
- `NOTIFICATION_DELIVERY_TIMEOUT_SECONDS` 默认 `8.0` 秒，小数可用，非法值回退默认，边界值 clamp 到 `0.1` 到 `60.0` 秒。
- `timeoutSeconds` 请求字段省略或为 `null` 时使用全局默认；合法数值按 `0.1` 到 `60.0` 秒 clamp 并持久化；非法类型返回 400 且不创建任务。
- 手动重试会重置当前任务 attempt 计数，但不会删除历史 attempts。
- 手动重试和批量重试都会增加 `deliveryRun`，且新 attempts 的 `attemptSequence` 继续全历史递增。
- 服务重启时 `delivering` 状态任务能回到 `queued` 或其他可投递状态，不永久卡住。
- 重启恢复后的任务 `lastError` 应说明上次投递被重启中断，且 `/health` 的 `queue.readyCount` 能包含恢复后的可领取任务。
- `maxAttempts` 仍限制在安全范围内，例如最小 1、最大 10。

### 安全

- same-origin `/mock/vendor/` 允许。
- localhost、127.0.0.1、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、169.254.0.0/16、`::1` 等非 mock 目标拒绝。
- 白名单 origin 只允许明确配置的外部供应商。
- 白名单初始 URL 发生 302/307/308 时，重定向后的 `Location` 也必须重新执行白名单和 SSRF 校验；跳转到 localhost、私网、link-local、metadata 或未授权 Origin 时不得成功投递。
- `NOTIFICATION_API_KEYS` 未设置时鉴权默认关闭；设置后只保护写接口，读接口、`/health`、`/api/stats`、静态资源和 mock vendor 仍可用于调试。
- 调用方 Key 不应写入任务、attempts、日志、截图、工单、`resolutionNote` 或复制 curl 示例。
- Header、Body、lastError、attempt error 不泄露敏感值。
- 展示脱敏不影响真实 HTTP 投递原文。

### 前端

- 首页、`app.js`、`styles.css` 都能正确加载。
- Health/Worker 状态小条能读取 `/health` 并展示健康、降级、数据库异常、Worker 异常或队列积压状态。
- 状态小条至少能安全处理 `status`、`serviceVersion`、`schemaVersion`、`database.ok/path`、`worker.alive`、`queue.counts`、`queue.readyCount`、`now`，字段缺失或 `null` 不应导致白屏。
- 状态小条能展示或兜底 Worker 启动时间、最近 tick、最近 claimed job、最近错误。
- `成功示例` 和 `失败示例` 使用当前页面 origin，不硬编码错误端口。
- 表单 `Request ID` 会进入提交 payload，重复提交同 requestId 命中幂等，详情展示 requestId。
- 详情抽屉能复制 `requestId` 和 `targetUrl`；字段为空或复制失败时有兜底提示，不白屏。
- `暂停自动刷新` 能停止和恢复列表轮询，且 `aria-pressed` 状态正确。
- `复制 curl` 能生成当前表单等价请求，JSON 错误时有提示。
- 启用 `NOTIFICATION_API_KEYS` 后，未带 Key 的页面写操作如果返回 `401`，页面应显示错误并保持可操作，不应白屏；复制 curl 不应自动包含真实 Key。
- 状态筛选 `全部`、`等待`、`重试`、`成功`、`失败` 都能刷新列表。
- 过滤表单支持 `status`、`eventType`、`sourceSystem`、`targetUrl`、`limit` 组合查询，`清空过滤` 能恢复默认列表。
- 过滤表单如果提供时间范围输入，应发送合法 `createdFrom/createdTo/updatedFrom/updatedTo`，非法时间有提示且保留当前列表。
- 分页和排序控件如果已接入页面，应能驱动 `offset`、`sort`、`order` 请求；控件未接入时，API 调试仍应可用。
- `导出 CSV` 如果已接入页面，应带上当前过滤、时间范围、分页和排序条件，下载失败时有提示，且不泄露敏感 Header 或完整 Body 原文。
- 任务卡片显示状态、目标 URL、来源系统、尝试次数、下次投递和错误信息。
- 详情弹窗能同时展示任务详情、requestId、failureType、attempts 的 `errorType`、`deliveryRun` 和 `attemptSequence`，加载失败时有提示，不白屏。
- `重新投递` 后列表和详情状态能刷新。
- `批量重试` 按钮能调用 `POST /api/notifications/retry`，展示 `count/items` 结果，并刷新列表。

### 自动化与人工抽查

- 每轮大改后运行：

```bash
python3 scripts/smoke_test.py
```

- 如果已有服务正在运行，运行：

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8001
```

- 至少人工完成一次 TC-S01、TC-F01、TC-H01 到 TC-H07、TC-R01、TC-R02、TC-L01 到 TC-L06、TC-A01、TC-A02、TC-A04、TC-A05、TC-U01 到 TC-U15、TC-M01 到 TC-M04、TC-K01 到 TC-K06、TC-E01 到 TC-E04、TC-V01 到 TC-V06、TC-T01 到 TC-T03、TC-P01 到 TC-P06、TC-W01 和 TC-W02。
- 检查浏览器控制台、服务端日志和数据库任务记录，确认没有新增异常。
- 专门记录一次 `delivering` 任务重启恢复结果，包括重启前状态、重启后状态、`lastError` 和后续 attempts。
- 记录本轮端口、提交时间、失败用例和修复结论，便于 50+ 轮后追踪回归来源。
