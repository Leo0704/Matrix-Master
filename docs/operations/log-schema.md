# 日志 Schema（结构化日志规范）

| 项 | 内容 |
|---|---|
| 适用对象 | 所有贡献者（Python / Rust / Android 三端） |
| 配套 | [monitoring-runbook.md §6](./monitoring-runbook.md) / [architecture/SDD.md](../architecture/SDD.md) |
| 状态 | 草案 v1（与 v0.6 代码同步） |

## 1. 设计目标

矩阵主控的所有运行日志采用**统一的 JSON 行格式**，便于：

- 跨端聚合（Python 后端 + Rust shell + Android APK 写同一份或可被同一查询消费）
- 字段级查询（dashboard 按 `service / trace_id / event / run_id` 过滤）
- 调用链串联（一个用户动作从入口到出口完整可追）

## 2. 字段定义

每条日志是**一行 JSON**，必填字段如下：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ts` | string | ✓ | ISO8601 UTC 毫秒精度，如 `"2026-07-09T08:23:45.123Z"` |
| `level` | string | ✓ | `debug` / `info` / `warn` / `error` |
| `service` | string | ✓ | 端标识：`matrix-backend` / `matrix-shell` / `matrix-apk` |
| `version` | string | ✓ | 端版本号（如 `"0.6.1"`） |
| `event` | string | ✓ | 点分事件名（见 §4） |
| `attrs` | object | ✓ | 业务字段（见 §5） |

强烈建议填：

| 字段 | 类型 | 说明 |
|---|---|---|
| `trace_id` | string | W3C trace context（32 字符 hex），跨进程串联 |
| `span_id` | string | W3C span id（16 字符 hex），单次跨度标识 |
| `run_id` | string | Agent run id（业务级） |
| `device_id` | string | 设备 id |
| `account_id` | string | 账号 id |
| `latency_ms` | number | 耗时 |
| `error_code` | string | 错误码（如 `RISK_BLOCKED` / `SELECTOR_NOT_FOUND`） |

> **白名单字段**（业务代码应优先使用这些 key，便于 dashboard 聚合）：
> `ts, level, service, version, event, trace_id, span_id, run_id, device_id, account_id, action, latency_ms, error_code`
>
> 其他字段可放在 `attrs` 下，但 dashboard 不会自动识别。

## 3. 各端实现

### 3.1 Python 后端（`matrix-backend`）

- **日志库**：`structlog` 24+ + 标准 `logging`
- **入口**：`backend/matrix/monitoring/logging.py::get_logger(name)`
- **格式**：JSON（生产） / ConsoleRenderer（dev）
- **文件**：`~/.matrix/logs/{YYYY-MM-DD}.jsonl`，单文件 100MB 滚动，保留 7 天
- **上下文**：`bind_context(trace_id=..., run_id=..., device_id=...)` 在请求入口注入

### 3.2 Rust shell（`matrix-shell`）

- **日志库**：`tracing` 0.1 + `tracing-subscriber`（env-filter + json）
- **入口**：`tracing::info!` / `#[tracing::instrument]`
- **格式**：JSON（生产） / pretty（dev）
- **输出**：stderr；与 Python 日志分文件但字段对齐
- **跨进程**：reqwest client 注入 `X-Request-ID` header，透传到 master

### 3.3 Android APK（`matrix-apk`）

- **日志库**：Timber 5.0.1 + 自定义 `Tree`
- **入口**：`Timber.i(...)` / `Timber.e(..., throwable)`
- **格式**：本地文件（兜底）+ 周期 POST 到 `http://<master>:8666/api/v1/logs`
- **周期**：与心跳对齐，30s 一次批量上传
- **透传**：入站 `X-Request-Id` header 写入当前 coroutine context

## 4. 事件命名规范

**点分命名**（`<subsystem>.<noun>.<verb>`）：

```
agent.run.start
agent.run.complete
agent.run.fail
device.pair.success
device.heartbeat.timeout
llm.call.start
llm.call.rate_limited
kb.publish.doc
chat.theme.confirmed
```

**规则**：

- 全小写，snake_case
- 用动词原形（start / complete / fail / success / timeout）
- 业务字段放在 `attrs` 下，**不要**拼进 event 名
- 错误用 `*.fail` 或 `*.error` 后缀，便于 dashboard 按前缀过滤

❌ 反例（**禁止**）：

```python
# 1. f-string — 字段全在字符串里，不可解析
log.info(f"agent run {run_id} started for device {device_id}")

# 2. printf-style — structlog 会静默丢弃 positional args，只剩字符串
log.info("agent.run.start run_id=%s device_id=%s", run_id, device_id)
#   → JSON 输出: {"event": "agent.run.start run_id=%s device_id=%s", ...}
#   → 字段全空
```

✅ 正例（**必须**）：

```python
log.info("agent.run.start", run_id=run_id, device_id=device_id)
# → JSON 输出: {"event": "agent.run.start", "run_id": "...", "device_id": "...", ...}
```

> **强制**：所有调用必须使用 kwargs 风格（`log.<level>(event, **fields)`）。
> structlog **不会**做 printf 格式化，positional args 会被静默丢弃。
> PR 2 负责把所有 41 处老调用点改成 kwargs 风格。

## 5. 业务字段（`attrs` 内容）

- 字段名一律 snake_case
- 数值类型保持原生（int / float），不要 string 化
- ID 类字段保持原始类型（UUID 字符串 / hex）
- 集合用 `list`，不要 stringify

```json
{
  "event": "kb.publish.doc",
  "attrs": {
    "doc_id": "9c8a1f...",
    "reviewer": "alice",
    "latency_ms": 234,
    "tags": ["fashion", "weekly"]
  }
}
```

## 6. 错误日志

错误日志必须包含 `exc_info` 字段（自动由 structlog `format_exc_info` processor 注入）：

```json
{
  "event": "agent.run.fail",
  "level": "error",
  "attrs": {
    "run_id": "...",
    "exc_type": "TimeoutError",
    "exc_message": "LLM call exceeded 30s",
    "exc_info": "Traceback (most recent call last): ..."
  }
}
```

**禁止**把 stacktrace 拼进 message 字符串。

## 7. 与现有代码的兼容

- `backend/matrix/monitoring/logging.py::LOG_FIELDS` 当前已包含 `ts / level / run_id / device_id / account_id / action / latency_ms / error_code`，**保持不变**
- 新增 `service / version / event / trace_id / span_id` 通过以下方式补齐：
  - `service` + `version`：在 `configure_logging()` 里硬编码（或读 `__version__`）
  - `trace_id` / `span_id`：由 OTel context + `MonitoringMiddleware` 注入
  - `event`：structlog 默认就输出，无需额外配置

## 8. 升级路径

### 阶段 1（PR 1，本文件）

- 写 `log-schema.md`（本文件）
- 不引入 PAF 兼容层——structlog 24+ 没有 `PositionalArgumentsFormatter`，且即便有也会让字段仍在字符串里
- 不破坏任何现有测试（仅文档改动）

### 阶段 2（PR 2）

- 38 个生产文件批量改 `logging.getLogger(__name__)` → `get_logger(__name__)`
- 41 处调用点强制改成 kwargs 风格（printf-style 不再支持）
- `LOG_FIELDS` 扩展为含 `service / version / event / trace_id / span_id`

### 阶段 3（PR 3-6）

- 验证 Python middleware trace_id 透传
- Rust 端引入 `tracing`
- Rust reqwest 注入 `X-Request-ID`
- Android 端 Timber + `/api/v1/logs` ingest

## 9. 验证手段

```bash
# 1. 字段完整性：每行 JSON 都有必填字段
cat ~/.matrix/logs/2026-07-09.jsonl | jq -r 'select(.ts == null or .service == null or .event == null)'

# 2. trace 串联：从一个 trace_id 看跨端日志
grep '"trace_id":"abc..."' ~/.matrix/logs/*.jsonl

# 3. 事件命名合规：不允许大写 / 下划线分隔的动词
grep -oE '"event":"[A-Z]' ~/.matrix/logs/*.jsonl

# 4. 字段填充率（业务字段不应为 null）
jq '.attrs | select(.run_id == null and .device_id == null and .account_id == null)' ~/.matrix/logs/*.jsonl | head
```