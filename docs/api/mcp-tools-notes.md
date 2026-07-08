# MCP Tools 补充说明

| 项 | 内容 |
|---|---|
| 配套 | [mcp-tools.schema.json](./mcp-tools.schema.json) |
| 说明 | 主 schema 中省略的非形式化内容（限速规则 / 调用生命周期），避免污染 JSON Schema |

## 1. 限速规则

| 维度 | 数值 |
|---|---|
| 每账号令牌桶 capacity | 30 |
| 每账号 refill 速率 | 0.033 / 秒（每 30s +1） |
| 活跃窗 | 09:00-23:00 设备本地时区 |
| 单设备日发布上限 | 5 |
| 单设备日互动上限 | 30 |
| 单账号日发布上限 | 3 |
| 单账号日互动上限 | 20 |

## 2. 工具调用生命周期

1. **init**：Agent 决定调用 tool，构造参数 + request_id
2. **validate**：主控校验参数 schema + 设备亲和 + 账号状态
3. **rate_limit**：经限速器
4. **sign**：主控用 HMAC 签名请求
5. **dispatch**：通过 Tailscale 发送到 APK
6. **execute**：APK 执行操作
7. **respond**：APK 回报结果
8. **record**：主控记录到 llm_usage / interactions / tasks
9. **retry**：失败时按错误分类策略重试

## 3. 跨文档引用

- 限速器实现：[../architecture/SDD.md §3.4](../architecture/SDD.md)
- 错误分类与重试策略：[../architecture/SDD.md §7](../architecture/SDD.md)
- 监控指标：[../operations/monitoring-runbook.md](../operations/monitoring-runbook.md)
