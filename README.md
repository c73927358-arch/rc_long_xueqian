# HTTP 通知投递服务 Demo

这是一个模拟企业内部外部 HTTP 通知投递服务的小型实现：

- 后端：`server.py`，Python 标准库实现，无第三方依赖。
- 数据库：SQLite，启动后自动生成 `notifications.db`。
- 前端：`public/`，由后端直接托管。
- 需求文档：[docs/requirements.md](docs/requirements.md)
- 设计说明：[docs/design-notes.md](docs/design-notes.md)
- 测试文档：[docs/test-plan.md](docs/test-plan.md)

## 设计摘要

本系统边界是内部业务系统到外部 HTTP(S) API 的通知投递层：负责接收、持久化、异步投递、失败重试、状态查询和人工补偿。第一版不解决精确一次投递、供应商配置中心、复杂认证签名、分布式高可用和完整安全治理，因为这些能力需要真实供应商契约、基础设施和运维配套，过早引入会掩盖核心投递问题。

当前投递语义选择“至少一次”。任务写入 SQLite 后才返回业务系统，后台 Worker 投递失败时按指数退避重试，超过最大次数后进入 `failed`，并保留错误原因；如果外部系统长期不可用，第一版通过有限自动重试和手动重试处理，生产化时再增加死信队列、批量重试、供应商级熔断和告警。

当前 Demo 没有引入 Kafka、RabbitMQ、Temporal 等中间件，原因是作业重点在工程判断和可靠性语义，本地零依赖更容易运行和验证。未来流量增长时，可以演进为 PostgreSQL/消息队列 + 多 Worker + 死信队列 + 可观测性平台。完整取舍见设计说明文档。

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

## API 示例

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
    "maxAttempts": 5
  }'
```

查看任务：

```bash
curl http://127.0.0.1:8000/api/notifications
```

手动重试：

```bash
curl -X POST http://127.0.0.1:8000/api/notifications/{id}/retry
```

## Mock 供应商

成功投递地址：

```text
http://127.0.0.1:8000/mock/vendor/crm
```

失败投递地址：

```text
http://127.0.0.1:8000/mock/vendor/crm?fail=1
```
