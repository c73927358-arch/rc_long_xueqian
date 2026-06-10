# 三 Agent 协作循环计划

## 1. 目标

围绕原始需求“内部服务接收业务系统提交的外部 HTTP(S) 通知请求，并尽可能可靠地投递到目标地址”，使用三个角色持续迭代：

- 需求发现 agent：持续发现当前系统还能补充的需求、边界、取舍和优先级。
- 代码编写 agent：把高价值、低冲突的需求转化为代码和文档变更。
- 测试 agent：设计测试矩阵、执行验证、反馈缺陷和风险。

协作方式采用循环推进：需求发现提出候选需求，代码编写选择可落地切片，测试 agent 验证并反馈，下一轮根据反馈继续调整。

## 2. 第一批修改候选

1. 增加供应商配置能力：用 `vendorKey` 引用预定义供应商，避免业务方每次传完整 URL/Header。
2. 增加目标 URL 白名单：限制只允许投递到受信域名，降低 SSRF 风险。
3. 增加每次投递尝试日志：保留每次 attempt 的状态码、耗时、错误。
4. 增加 Dashboard 汇总指标：成功数、失败数、等待重试数、平均尝试次数、最近错误。
5. 增加任务详情页或详情弹窗：查看完整 Header、Body、尝试日志和重试信息。
6. 增加批量重试失败任务：支持按状态批量重试。
7. 增强请求幂等说明：强化 `requestId` 文档和 UI，提示下游仍需幂等。
8. 增加投递超时配置：按任务或供应商配置 timeout。
9. 增加失败分类：区分 `http_error`、`network_error`、`timeout`、`invalid_target`。
10. 增加通知搜索/过滤：按事件类型、来源系统、目标地址、状态、时间范围过滤。
11. 增加敏感信息脱敏：隐藏 `Authorization`、`Token`、`Secret` 等敏感 Header。
12. 增加一键 smoke test：验证成功投递、失败重试、非法输入、手动重试。
13. 增加服务启动自检：检查数据库、静态目录、mock vendor、Worker 状态。
14. 增加重试策略配置说明：明确指数退避参数、最大次数上限和长期失败处理。
15. 增加 API 调用示例集合：覆盖广告注册、CRM 更新、库存变更三个原始场景。

## 3. 第一轮优先级

第一轮优先选择“可提升可靠性、测试可观察性、风险较低”的修改：

| 优先级 | 修改 | 判断依据 |
| --- | --- | --- |
| P0 | 每次投递尝试日志 | 当前只记录最近错误，不利于排障和证明重试行为 |
| P0 | attempts 查询接口 | 方便前端和测试验证后台投递过程 |
| P1 | smoke test 脚本 | 降低手工验证成本，便于每轮回归 |
| P1 | 测试矩阵补充 | 让后续迭代有明确验收口径 |
| P2 | 前端详情展示 | 等后端 attempts 接口稳定后再做 |

## 4. 8 小时循环节奏

计划按 8-10 分钟一个小循环推进，目标 8 小时内尽量完成 50 轮以上的小修改。每轮不追求大而全，而是保持“一个明确需求、一个有限改动、一次可验证结果”。

| 时间段 | 需求发现 agent | 代码编写 agent | 测试 agent | 主线程整合 |
| --- | --- | --- | --- | --- |
| 0-1 小时 | 补齐可靠性、排障、安全 P0 | attempt 日志、详情、smoke test、URL 安全 | 成功/失败/重试/白屏调试 | 整合第一批高价值能力 |
| 1-2 小时 | 聚焦前端调试和运营可见性 | 指标、过滤、详情刷新、批量操作 | 页面交互和状态判断 | 修复 UI 与 API 契约偏差 |
| 2-3 小时 | 聚焦供应商适配边界 | timeout、失败分类、mock vendor 增强 | 超时、网络错误、状态码矩阵 | 收敛重试语义 |
| 3-4 小时 | 聚焦安全与数据治理 | 脱敏、白名单、请求限制、错误保护 | SSRF、敏感字段、非法输入 | 梳理上线边界 |
| 4-5 小时 | 聚焦运维和恢复能力 | 自检、重启恢复、死信/批量重试 | 重启、卡死、历史 attempts | 强化可靠性说明 |
| 5-6 小时 | 聚焦 API 契约和文档 | 示例、OpenAPI 风格说明、FAQ | 文档走读和 curl 用例 | 降低接入成本 |
| 6-7 小时 | 聚焦质量和代码整洁 | 小范围重构、配置统一、错误文案 | 回归和边界补测 | 避免复杂度失控 |
| 7-8 小时 | 收敛与验收 | 修复剩余问题、整理变更 | 全量 smoke、人工白屏验证 | 输出最终总结和后续路线 |

## 5. 第一轮验收标准

- 不破坏现有 `POST /api/notifications`、`GET /api/notifications`、`POST /api/notifications/{id}/retry`。
- 成功投递任务仍能进入 `succeeded`。
- 失败投递任务仍能进入 `waiting_retry` 或 `failed`。
- 新增尝试日志后，可以查询到每次投递尝试。
- smoke test 可以在本地服务启动后运行并给出清晰结果。
- 文档说明当前增强仍然是“至少一次”语义，不误导为精确一次。

## 6. 50+ 轮候选修改任务板

这些任务按优先级和依赖关系排列。每一轮可以选择 1 个小切片落地；当某个任务过大时，拆成 API、前端、测试、文档多个轮次。

| 轮次 | 修改点 | 主要收益 | 状态 |
| --- | --- | --- | --- |
| 01 | 增加 `notification_attempts` 表 | 记录每次投递尝试 | 已完成 |
| 02 | 增加 `GET /api/notifications/{id}/attempts` | 支持排障和前端详情 | 已完成 |
| 03 | 增加 smoke test 脚本 | 每轮快速回归 | 已完成 |
| 04 | 前端任务卡片增加详情入口 | 白屏可查看单任务 | 已完成 |
| 05 | 详情抽屉展示请求和 attempts | 判断成功/失败更直观 | 已完成 |
| 06 | 支持 `NOTIFICATION_DB_PATH` | 测试可使用临时数据库 | 已完成 |
| 07 | URL 精确 Origin 白名单 | 降低误投递和 SSRF 风险 | 已完成 |
| 08 | same-origin mock vendor 例外 | 保留本地演示能力 | 已完成 |
| 09 | 拦截 localhost/私网/metadata 地址 | 安全边界最小闭环 | 已完成 |
| 10 | API 展示 Header 脱敏 | 避免泄露 Token/API Key | 已完成 |
| 11 | API 展示 JSON Body 递归脱敏 | 避免泄露业务密钥 | 已完成 |
| 12 | attempts/error 脱敏 | 避免错误信息泄密 | 已完成 |
| 13 | smoke test 覆盖 SSRF 拒绝 | 防止安全能力回退 | 已完成 |
| 14 | smoke test 覆盖脱敏 | 防止页面/API 泄密 | 已完成 |
| 15 | 前端汇总指标 | 白屏快速判断系统状态 | 已完成 |
| 16 | 详情抽屉刷新按钮 | 查看异步状态变化 | 已完成 |
| 17 | 详情抽屉重新投递按钮 | 在排障页直接补偿 | 已完成 |
| 18 | attempt 状态文案区分任务状态 | 避免“本次失败/最终失败”混淆 | 已完成 |
| 19 | 详情展示完整时间字段 | 判断排队、投递、完成时间 | 已完成 |
| 20 | 测试文档补 attempts API 矩阵 | 让测试覆盖可复用 | 已完成 |
| 21 | 测试文档补白屏调试步骤 | 降低手工验证成本 | 已完成 |
| 22 | 测试文档补 SSRF/脱敏用例 | 明确上线前风险检查 | 已完成 |
| 23 | 增加失败分类字段 | 区分 HTTP、网络、超时、安全失败 | 已完成 |
| 24 | mock vendor 支持状态码参数 | 更方便测试不同失败 | 已完成 |
| 25 | mock vendor 支持延迟参数 | 测试超时和投递中状态 | 已完成 |
| 26 | 配置默认投递超时 | 防止 worker 长时间阻塞 | 已完成 |
| 27 | 供应商级 timeout 示例 | 演进到 vendorKey 的前置能力 | 已完成 |
| 28 | 手动重试保留全局递增 attempt 序号 | 避免历史编号重复 | 已完成 |
| 29 | 手动重试记录 retry batch/run id | 区分第几轮人工补偿 | 已完成 |
| 30 | 批量重试失败任务 | 供应商恢复后集中补偿 | 已完成 |
| 31 | 失败任务死信说明/状态 | 长期失败后可运营处理 | 已完成 |
| 32 | 列表分页参数 | 支持更多任务排查 | 已完成 |
| 33 | 列表按 eventType 过滤 | 快速定位业务事件 | 已完成 |
| 34 | 列表按 sourceSystem 过滤 | 快速定位来源系统 | 已完成 |
| 35 | 列表按 targetUrl 关键词过滤 | 快速定位供应商 | 已完成 |
| 36 | 列表按时间范围过滤 | 支持事故窗口排查 | 已完成 |
| 37 | 前端搜索表单 | 页面可直接筛选任务 | 已完成 |
| 38 | 列表自动刷新可暂停 | 避免排查时内容跳动 | 已完成 |
| 39 | Worker 启动自检 | 暴露后台投递线程状态 | 已完成 |
| 40 | `/health` 增加数据库和 worker 信息 | 运维可快速判断健康 | 已完成 |
| 41 | 重启恢复测试 | 验证 `delivering` 回队列 | 已完成 |
| 42 | stuck delivering 租约超时回收 | 不依赖进程重启恢复 | 已完成 |
| 43 | requestId 幂等 UI 输入 | 白屏演示重复提交 | 已完成 |
| 44 | requestId 幂等 smoke test | 防止重复任务回退 | 已完成 |
| 45 | API 示例覆盖广告注册 | 对齐原始需求场景 | 已完成 |
| 46 | API 示例覆盖 CRM contact | 对齐原始需求场景 | 已完成 |
| 47 | API 示例覆盖库存变更 | 对齐原始需求场景 | 已完成 |
| 48 | README 补安全配置说明 | 接入方知道如何配置 | 已完成 |
| 49 | README 补白屏调试说明 | 用户知道看哪个状态 | 已完成 |
| 50 | 设计文档补过度设计取舍 | 回答作业评估重点 | 已完成 |
| 51 | 设计文档补 8 小时迭代总结 | 展示工程判断过程 | 已完成 |
| 52 | 增加简单 API 契约文档 | 接入业务系统更清晰 | 已完成 |
| 53 | 增加错误响应码说明 | 业务方知道如何处理提交失败 | 已完成 |
| 54 | 前端错误提示统一 | 页面调试更稳定 | 已完成 |
| 55 | JSON 输入格式化按钮 | 降低白屏手工输入成本 | 已完成 |
| 56 | 复制 curl 按钮 | 快速复现实验请求 | 已完成 |
| 57 | 任务详情复制 ID/URL | 便于排障协作 | 已完成 |
| 58 | attempts 列表过长折叠 | 手动重试多次仍可读 | 已完成 |
| 59 | 长错误文本换行优化 | 防止详情抽屉溢出 | 已完成 |
| 60 | 最终全量回归和推送 | 收敛长周期产物 | 已完成 |
| 61 | redirect 目标二次校验 | 防止供应商 URL 跳转到内网 | 已完成 |
| 62 | 简单 `vendorKey` 配置示例 | 减少业务方重复填写 URL/Header | 已完成 |
| 63 | 单任务 timeout 参数 | 慢供应商可局部调整超时 | 已完成 |
| 64 | 死信任务标记与说明 | 长期失败后转人工处理 | 已完成 |
| 65 | `/api/stats` 汇总接口 | 前端和运维可读统一指标 | 已完成 |
| 66 | 列表 offset 分页 | 大量任务排查不丢数据 | 已完成 |
| 67 | 任务导出 CSV | 事故复盘可保存现场 | 已完成 |
| 68 | 详情复制 requestId/targetUrl | 便于多人排障协作 | 已完成 |
| 69 | OpenAPI 风格接口说明 | 业务系统接入更低成本 | 已完成 |
| 70 | schema/version 健康字段 | 排查“旧服务/新前端”错配 | 已完成 |
| 71 | 列表排序参数 | 可按创建时间或更新时间排查 | 已完成 |
| 72 | 人工重试审计字段 | 记录是谁/何时触发补偿 | 已完成 |
| 73 | worker 并发配置 | 高峰期提高吞吐 | 已完成 |
| 74 | Prometheus 文本指标草案 | 生产监控演进准备 | 已完成 |
| 75 | 调用方 API Key 鉴权草案 | 生产安全边界演进准备 | 已完成 |

## 7. 当前循环记录

| 批次 | 需求发现 | 实现 | 测试 | 结论 |
| --- | --- | --- | --- | --- |
| Round A | 优先补 attempts、详情、URL 安全、脱敏 | attempts 表/API、smoke test、详情抽屉 | py_compile、smoke test、attempts 404 | attempts 和详情可用，进入安全和前端调试增强 |
| Round B | 明确 URL 白名单、SSRF、脱敏验收 | URL 安全、展示脱敏、前端汇总和详情操作 | 当前实例 smoke test、静态资源检查 | 安全边界和白屏调试能力可用，进入失败分类和 mock vendor 增强 |
| Round C | 聚焦失败分类、timeout、mock vendor 可控性和 requestId 调试 | failureType/errorType、mock status/delay、timeout 配置、requestId、暂停刷新、复制 curl | py_compile、node --check、临时服务完整 smoke、8001 smoke | 完成，进入 attempts 语义和批量操作 |
| Round D | 聚焦 attempts 全局语义、批量补偿和列表过滤 | attemptSequence/deliveryRun、批量重试 API、列表过滤 API、前端过滤表单 | py_compile、node --check、临时服务完整 smoke、8001 smoke | 完成，进入健康检查和恢复能力 |
| Round E | 聚焦健康检查、启动自检、Worker 状态和重启恢复 | `/health` health schema、worker 状态、启动自检、前端健康条、重启恢复 smoke | py_compile、node --check、临时服务完整 smoke、8001 smoke | 完成，进入 stuck delivering 回收、API 示例和调试体验增强 |
| Round F | 聚焦不依赖重启的恢复、幂等回归、API 接入示例和前端调试效率 | delivering lease 回收、requestId 幂等响应和 smoke、JSON 格式化、详情复制、attempts 滚动、三类业务 API 示例 | py_compile、node --check、git diff --check、浏览器白屏验证、临时服务完整 smoke、8001 smoke | 完成，进入 redirect 校验、vendorKey、分页和更多运维能力 |
| Round G | 聚焦 redirect 安全、stats 运维接口、vendorKey 演进和健康条信息密度 | SafeRedirectHandler、mock redirect、`/api/stats`、stats 前端展示、vendorKey/API 契约文档、redirect 安全测试 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 smoke、浏览器 health/stats 验证 | 完成，进入分页、排序、死信和导出能力 |
| Round H | 聚焦列表分页、排序和导出，增强多任务排障能力 | `offset/limit` 分页、排序白名单、CSV 导出、前端分页/排序/导出控件、契约和测试文档 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 smoke、浏览器分页验证 | 完成，进入死信/人工处理和审计能力 |
| Round I | 聚焦死信/人工接管、人工动作审计和 shared DB 下的测试副作用 | `dead_letter` 状态、dead-letter API、人工字段、前端转人工、接口字段别名兼容、`--base-url` smoke 隔离批量重试副作用 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 冒烟模式、浏览器死信筛选和详情验证 | 完成，进入时间范围过滤、健康 schema 版本和详情复制增强 |
| Round J | 聚焦事故窗口排查、前后端错配诊断和详情复制兜底 | `createdFrom/createdTo/updatedFrom/updatedTo` 列表和 CSV 过滤、`serviceVersion/schemaVersion`、前端时间筛选、版本展示、详情复制字段兜底、文档和测试用例 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 冒烟模式、浏览器时间筛选和版本字段验证 | 完成，进入 per-notification timeout、OpenAPI 说明和监控演进 |
| Round K | 聚焦慢供应商差异化超时、接入契约结构化和监控演进边界 | `timeoutSeconds`、旧库迁移、任务级 timeout 投递、前端超时输入和详情展示、OpenAPI 风格接口目录、Prometheus 文本指标草案 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 冒烟模式、真实 API 短 timeout 验证 | 完成，进入 worker 并发配置和调用方鉴权草案 |
| Round L | 聚焦 worker 并发配置、可观测性和 SQLite 边界 | `NOTIFICATION_WORKER_CONCURRENCY`、多 worker 线程启动、health 并发字段、前端 Concurrency/Threads/Alive 展示、并发取舍文档 | py_compile、node --check、git diff --check、临时服务完整 smoke、8001 冒烟模式、浏览器 health 并发字段验证 | 完成，进入调用方 API Key 鉴权草案和最终收敛 |
| Round M | 聚焦内部写入口的最小鉴权和最终收敛 | `NOTIFICATION_API_KEYS` 可选共享 Key、写接口 401 保护、前端 API Key 输入/sessionStorage/curl header、契约和测试文档 | py_compile、node --check、git diff --check、临时服务完整 smoke、鉴权短流程、8001 浏览器 API Key 输入验证 | 完成，本轮结束后停止新增需求，进入现有功能回归 |

## 8. Round F agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Tesla | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | stuck delivering 租约回收、requestId 幂等回归 |
| Hegel | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | JSON 格式化、详情复制、attempts 可读性、长错误换行 |
| Lorentz | README、`docs/design-notes.md`、`docs/test-plan.md`、可选 `docs/api-examples.md` | 后端、前端、脚本 | 三个原始业务场景接入示例、错误码、安全配置、测试用例 |

主线程只更新 `docs/agent-loop-plan.md` 和做最终整合验证，避免和子 agent 写同一批文件。

## 9. Round G agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Ptolemy | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | redirect 目标二次校验、mock redirect、`/api/stats`、对应 smoke |
| Newton | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | health 展示 lease/expired delivering，可选 stats 区域，字段缺失兜底 |
| Bacon | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-examples.md`、可选 `docs/api-contract.md` | 后端、前端、脚本 | vendorKey 设计、API 契约、redirect 安全测试、stats 运维判断 |

## 10. Round H agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Hooke | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | 列表 offset 分页、排序、CSV 导出、对应 smoke |
| Meitner | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | 排序控件、上一页/下一页、导出 CSV 按钮，兼容旧响应 |
| Maxwell | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-examples.md`、`docs/api-contract.md` | 后端、前端、脚本 | 分页/排序/导出契约、测试用例、offset vs cursor 取舍 |

## 11. Round I agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Euclid | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | `dead_letter` 状态、人工动作审计字段、dead-letter API、smoke |
| Dalton | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | dead_letter 状态展示、标记死信按钮、人工动作字段展示 |
| Hypatia | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-contract.md`、`docs/api-examples.md` | 后端、前端、脚本 | 死信/人工处理契约、测试、系统边界和演进 |

## 12. Round J agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Backend | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | 列表/CSV 支持 created/updated 时间范围过滤，`/health` 和 `/api/stats` 增加 schema/version 字段，对应 smoke |
| Frontend | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | 前端增加时间范围筛选，详情复制 requestId/targetUrl，兼容 schema/version 展示 |
| Docs | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-contract.md`、`docs/api-examples.md` | 后端、前端、脚本、`docs/agent-loop-plan.md` | 时间范围过滤、schema/version、详情复制的使用说明和测试用例 |

## 13. Round K agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Kierkegaard | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | 单任务 `timeoutSeconds`、旧库迁移、投递 timeout 覆盖、对应 smoke |
| Mill | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | 创建表单 timeout 输入、详情 timeout 展示、示例清空 timeout |
| Franklin | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-contract.md`、`docs/api-examples.md` | 后端、前端、脚本、`docs/agent-loop-plan.md` | timeout 契约、OpenAPI 风格接口说明、Prometheus 文本指标草案 |

## 14. Round L agent 分工

| Agent | 负责范围 | 不触碰范围 | 目标 |
| --- | --- | --- | --- |
| Backend | `server.py`、`scripts/smoke_test.py` | `public/*`、README、docs | worker 并发配置草案落地为可配置 worker 数，保持 SQLite 写入安全，并补 smoke/health 字段 |
| Frontend | `public/index.html`、`public/app.js`、`public/styles.css` | 后端、脚本、docs | health 区展示 worker 并发/配置字段，缺字段兼容 |
| Docs | README、`docs/design-notes.md`、`docs/test-plan.md`、`docs/api-contract.md`、`docs/api-examples.md` | 后端、前端、脚本、`docs/agent-loop-plan.md` | worker 并发取舍、配置示例、测试用例和未来队列演进说明 |
